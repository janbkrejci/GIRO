"""The plan of ``giro config set|remove|move``: the change, its blockers, validation and digest.

Planning writes nothing; ``execute`` writes a plan.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from aifactory import yamlload
from aifactory.backlog.derived import effective_workflow
from aifactory.backlog.loader import iter_tasks
from aifactory.backlog.snapshot import load_backlog_at
from aifactory.config.errors import ConfigError, ConfigIssue
from aifactory.config.loader import AGENTS_FILE, EXTENSIONS_DIR, ROLES_FILE
from aifactory.config.settings import CONFIG_FILE, ProjectSettings, parse_project_settings
from aifactory.config.source import FACTORY_DIR, OverlaySource
from aifactory.config.yamledit import edit_yaml, roster_add, roster_remove, roster_set
from aifactory.engine.role_registry import Issue
from aifactory.harness import CLI_BINARIES, THINKING_LEVELS, canonical
from aifactory.harness.check import binary_name, installed_path
from aifactory.library.agent import expand_writes
from aifactory.library.config_edit.state import (
    Change,
    Command,
    RepoState,
    Target,
    _yaml,
    read_state,
)
from aifactory.library.inherit import library_snapshot, with_item
from aifactory.library.install import _repo_root
from aifactory.library.install_commit import _dirty, _store, validate_source
from aifactory.library.load import check_repo_item, library_path
from aifactory.library.model import ITEM_TYPES, AgentDefaults, Item, ItemType, check_name
from aifactory.library.store import LibraryStoreError, NewFile, SeedItem, import_items
from aifactory.providers import git
from aifactory.providers.base import ProviderError
from aifactory.providers.publish import (
    DIRECT,
    PR,
    Blocker,
    PlannedFile,
    PublishPlan,
    direct_blockers,
    plan_digest,
    run_blocker,
    uses_pr,
)
from aifactory.workflow.model import RoleStep, WorkflowError, walk
from aifactory.workflow.parse import parse_workflow

if TYPE_CHECKING:
    from aifactory.library.inherit import LibrarySnapshot
    from aifactory.library.store import LibraryPlan


def _diff(before: RepoState, after: RepoState, change: Change) -> None:
    """Put the paths that differ between the two states into `change`."""
    for path in sorted(set(before.files) | set(after.files)):
        old, new = before.files.get(path), after.files.get(path)
        old_exe, new_exe = path in before.executable, path in after.executable
        if old == new and (new is None or old_exe == new_exe):
            continue
        change.files[path] = new
        if new is not None and new_exe:
            change.executable.add(path)


BINDING_KEYS = ("harness", "model", "thinking", "tools", "writes", "color")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def _bindings(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    entry = entry or {}
    out = {k: entry.get(k) for k in BINDING_KEYS}
    if out["harness"] is None and entry.get("coding_agent") is not None:
        out["harness"] = entry.get("coding_agent")
    return out


def _edit_agents(state: RepoState, edit: Any) -> bytes:

    data = state.files.get(AGENTS_FILE, b"")
    try:
        text = edit_yaml(data.decode("utf-8"), edit)
    except (UnicodeDecodeError, ValueError, KeyError) as exc:
        raise LibraryStoreError("invalid_config", f"{AGENTS_FILE}: {exc}") from exc
    return text.encode("utf-8")


def _check_type(type: str, name: str, what: str = "name") -> ItemType:
    if type not in ITEM_TYPES:
        raise LibraryStoreError("invalid_value", f"unknown item type {type!r}: {list(ITEM_TYPES)}")
    if not check_name(name):
        raise LibraryStoreError(
            "invalid_value", f"invalid {what} {name!r}: [a-z0-9][a-z0-9-]{{0,47}}"
        )
    return type


def _check_harness(value: str) -> str:
    try:
        return canonical(value)
    except ValueError as exc:
        raise LibraryStoreError("invalid_value", f"--harness {value!r}: {exc}") from exc


def _check_thinking(value: str) -> str:

    if value not in THINKING_LEVELS:
        raise LibraryStoreError(
            "invalid_value",
            f"--thinking {value!r}: unknown thinking level, available: {list(THINKING_LEVELS)}",
        )
    return value


# ── set ───────────────────────────────────────────────────────────────────────


def _list(value: str, what: str) -> list[str] | None:
    if value.strip() == "-":
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def plan_set(
    state: RepoState,
    library: LibrarySnapshot,
    type: str,
    slot: str,
    *,
    harness: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    tools: str | None = None,
    writes: str | None = None,
    color: str | None = None,
) -> Change:
    """The change of ``config set agent SLOT``: only the bindings in ``agents.yaml``."""

    if type != "agent":
        raise LibraryStoreError("invalid_value", f"config set takes agents only, not {type!r}")
    inherited = state.roster_entry(slot) is None
    if inherited and slot not in library.agents:
        raise LibraryStoreError(
            "unknown_item", f"no agent {slot!r} in {AGENTS_FILE} nor in the {library.label}"
        )
    values: dict[str, Any] = {}
    if harness is not None:
        values["harness"] = _check_harness(harness)
    if model is not None:
        if not model.strip():
            raise LibraryStoreError("invalid_value", "--model must not be empty")
        values["model"] = model.strip()
    if thinking is not None:
        values["thinking"] = _check_thinking(thinking)
    if tools is not None:
        values["tools"] = _list(tools, "tools")
    if writes is not None:
        entries = _list(writes, "writes")
        try:
            values["writes"] = expand_writes(entries, state.settings)
        except ValueError as exc:
            raise LibraryStoreError("invalid_value", f"--writes: {exc}") from exc
    if color is not None:
        if color.strip() == "-":
            values["color"] = None
        elif not _COLOR.fullmatch(color.strip()):
            raise LibraryStoreError("invalid_value", f"--color {color!r}: use #rrggbb")
        else:
            values["color"] = color.strip()
    if not values:
        raise LibraryStoreError(
            "invalid_value",
            "nothing to set: give --harness, --model, --thinking, --tools, --writes or --color",
        )
    if inherited:
        entry = {"name": slot, **{k: v for k, v in values.items() if v is not None}}
        data = _edit_agents(state, lambda doc: roster_add(doc, entry))
    else:
        data = _edit_agents(state, lambda doc: roster_set(doc, slot, values))
    change = Change(keys=list(values))
    after = state.apply({AGENTS_FILE: data}, set())
    _diff(state, after, change)
    change.bindings[slot] = _bindings(after.roster_entry(slot))
    return change


# ── remove ────────────────────────────────────────────────────────────────────


def _uses(state: RepoState, type: ItemType, name: str) -> list[str]:
    """What in the repo (or the backlog in base) needs the item."""
    if type == "agent":
        return _agent_uses(state, name)
    if type == "workflow":
        return [f"backlog task {tid}" for tid, wf in _base_tasks(state) if wf == name]
    out: list[str] = []
    for entry in state.roster:
        if type == "skill":
            bound = entry.get("skills") or []
            if isinstance(bound, list) and name in bound:
                out.append(f"agent {entry['name']}")
            continue
        prefix = f"{EXTENSIONS_DIR}/{name}"
        used = entry.get("harness_engineering")
        paths = used if isinstance(used, list) else [used] if used else []
        if any(str(p).rstrip("/") == prefix or prefix + "/" in str(p) for p in paths):
            out.append(f"agent {entry['name']}")
    return out


def _agent_uses(state: RepoState, name: str) -> list[str]:

    out: list[str] = []
    roles = state.roles
    for wf_name, data in state.workflows().items():
        raw = _yaml(data)
        try:
            workflow = parse_workflow(raw, roles)
        except WorkflowError:
            continue
        for step in walk(workflow.steps):
            if isinstance(step, RoleStep) and step.role.agent == name:
                out.append(f"workflow {wf_name} step {step.path}")
    raw = _yaml(state.files.get(ROLES_FILE))
    declared = raw.get("roles") if isinstance(raw, dict) else None
    if isinstance(declared, dict):
        for role, spec in declared.items():
            if isinstance(spec, dict) and spec.get("agent") == name:
                out.append(f"roles.yaml role {role}")
    return out


def _base_tasks(state: RepoState) -> list[tuple[str, object]]:
    """(id, effective workflow) of each task of the backlog in base (empty when unreadable)."""

    root, sha = state.root, state.base_sha
    config = git.blob_at(root, sha, CONFIG_FILE)
    if config is None:
        return []
    issues: list[ConfigIssue] = []
    text = git.read_blob(root, config[1]).decode("utf-8", "replace")
    settings = parse_project_settings(text, CONFIG_FILE, issues)
    if settings is None:
        return []
    try:
        backlog = load_backlog_at(root, sha, settings)
    except (ConfigError, OSError, ValueError, RuntimeError):
        return []
    return [(str(t.id), effective_workflow(t)) for t in iter_tasks(backlog)]


def _removal(state: RepoState, type: ItemType, name: str) -> dict[str, bytes | None]:
    files: dict[str, bytes | None] = dict.fromkeys(state.item_paths(type, name))
    if type == "agent" and state.roster_entry(name) is not None:
        files[AGENTS_FILE] = _edit_agents(state, lambda doc: roster_remove(doc, name))
    return files


def plan_remove(state: RepoState, library: LibrarySnapshot, type: str, name: str) -> Change:
    """The change of ``config remove``; see the module docstring."""
    kind = _check_type(type, name)
    if not state.has(kind, name):
        raise LibraryStoreError("unknown_item", f"no {kind} {name!r} in the repo")
    after = state.apply(_removal(state, kind, name), set())
    reasons = [] if name in library.of(kind) else _uses(after, kind, name)
    if reasons:
        raise LibraryStoreError(
            "in_use",
            f"{kind} {name!r} is used by: {', '.join(reasons)}",
            data={"type": kind, "name": name, "used_by": reasons},
        )
    change = Change()
    change.removed.append({"type": kind, "name": name, "reason": "requested"})
    _diff(state, after, change)
    return change


# ── move ──────────────────────────────────────────────────────────────────────


def repo_item(state: RepoState, kind: ItemType, name: str) -> Item:
    """The copy of the item in the repo state; ``unknown_item`` or ``invalid_item``."""

    if not state.item_paths(kind, name):
        raise LibraryStoreError("unknown_item", f"the repo has no own copy of {kind} {name!r}")
    with state.materialized() as tmp:
        item, issues = check_repo_item(tmp, kind, name)
    if item is None or issues:
        raise LibraryStoreError(
            "invalid_item", f"the {kind} {name!r} in the repo is not valid", issues=issues
        )
    return item


def _unexpand(writes: list[str], settings: ProjectSettings) -> list[str]:
    out: list[str] = []
    for entry in writes:
        for variable, value in (
            ("$specs_dir/", settings.specs_dir),
            ("$docs_dir/", settings.docs_dir),
        ):
            prefix = value.rstrip("/") + "/"
            if entry.startswith(prefix):
                entry = variable + entry[len(prefix) :]
                break
        out.append(entry)
    return out


def _agent_yaml(state: RepoState, library: LibrarySnapshot, item: Item) -> bytes:
    """``agent.yaml`` of a moved agent: the library defaults with the repo purpose, else
    the roster bindings as defaults."""
    known = library.agents.get(item.name)
    defaults: dict[str, Any] = {}
    if known is not None and known.defaults is not None:
        defaults = known.defaults.model_dump(mode="json", exclude_defaults=True)
    else:
        binding = _bindings(state.roster_entry(item.name))
        for key in ("harness", "model", "thinking", "color"):
            if isinstance(binding.get(key), str):
                defaults[key] = binding[key]
        for key in ("tools", "writes"):
            value = binding.get(key)
            if isinstance(value, list):
                values = [str(v) for v in value]
                defaults[key] = _unexpand(values, state.settings) if key == "writes" else values
    data: dict[str, Any] = {"purpose": item.purpose}
    if defaults:
        data["defaults"] = defaults
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")


def _library_item(item: Item, agent_yaml: bytes | None) -> SeedItem:
    """The files of `item` in the library layout."""

    rel = library_path(item.type, item.name)
    files: list[NewFile] = []
    if item.type == "agent":
        files.append(NewFile(f"{rel}/agent.yaml", False, agent_yaml or b""))
        for prompt in ("system.md", "user.md"):
            found = item.file(prompt)
            files.append(
                NewFile(
                    f"{rel}/{prompt}",
                    bool(found and found.executable),
                    found.data if found else b"",
                )
            )
    elif item.type == "workflow":
        files.append(NewFile(rel, False, item.files[0].data))
    else:
        files += [NewFile(f"{rel}/{f.path}", f.executable, f.data) for f in item.files]
    return SeedItem(item.type, item.name, item.version, tuple(files))


def plan_move(state: RepoState, library: LibrarySnapshot, type: str, name: str) -> Change:
    """The change of ``config move``: the library item to write and the repo copy to delete.

    An agent's roster entry keeps only its bindings (an override of the library agent);
    an entry left with only its name is removed.
    """

    kind = _check_type(type, name)
    if library.commit is None:
        raise LibraryStoreError(
            "library_missing",
            "there is no library to move into; create one with giro library init "
            "(or giro library clone)",
        )
    item = repo_item(state, kind, name)
    change = Change(moved=item)
    agent_yaml = _agent_yaml(state, library, item) if kind == "agent" else None
    if agent_yaml is not None:
        defaults = yamlload.safe_load(agent_yaml).get("defaults") or {}
        change.moved = replace(item, defaults=AgentDefaults.model_validate(defaults))
    change.library_items.append(_library_item(item, agent_yaml))
    files: dict[str, bytes | None] = dict.fromkeys(state.item_paths(kind, name))
    entry = state.roster_entry(name) if kind == "agent" else None
    if entry is not None:
        rest = [k for k in entry if k not in ("name", "purpose")]
        if rest:
            files[AGENTS_FILE] = _edit_agents(
                state, lambda doc: roster_set(doc, name, {"purpose": None})
            )
        else:
            files[AGENTS_FILE] = _edit_agents(state, lambda doc: roster_remove(doc, name))
    change.removed.append({"type": kind, "name": name, "reason": "moved"})
    _diff(state, state.apply(files, set()), change)
    return change


# ── plan ──────────────────────────────────────────────────────────────────────


@dataclass
class ConfigPlan:
    command: Command
    type: str
    name: str
    target: Target
    state: RepoState
    change: Change
    publish: PublishPlan
    issues: list[Issue]
    warnings: list[str]
    library: LibrarySnapshot
    library_plan: LibraryPlan | None = None  # move: the library write, made first

    @property
    def blockers(self) -> tuple[Blocker, ...]:
        return self.publish.blockers

    @property
    def library_changed(self) -> bool:
        return self.library_plan is not None and bool(self.library_plan.files)

    @property
    def changed(self) -> bool:
        return bool(self.publish.files) or self.library_changed

    def to_json(self) -> dict[str, Any]:
        change = self.change
        return {
            "repo": str(self.state.root),
            "command": self.command,
            "type": self.type,
            "name": self.name,
            **self.publish.to_json(),
            "target": self.target,
            "paths": [f.path for f in self.publish.files],
            "removed": change.removed,
            "bindings": change.bindings,
            "changed": self.changed,
            "validation": {"ok": not self.issues, "issues": [i.to_dict() for i in self.issues]},
            "warnings": self.warnings,
            "library": self.library.commit,
            **(
                {"library_plan": self.library_plan.to_json() if self.library_plan else None}
                if self.command == "move"
                else {}
            ),
        }


def _planned_files(state: RepoState, change: Change) -> list[PlannedFile]:
    files: list[PlannedFile] = []
    for path in sorted(change.files):
        new = change.files[path]
        old = state.files.get(path)
        if old is None and new is None:
            continue
        old_blob: str | None = None
        if old is not None:
            old_blob = state.blobs.get(path) or git.hash_blob(state.root, old, write=False)
        mode = None if new is None else "100755" if path in change.executable else "100644"
        old_mode = None if old is None else "100755" if path in state.executable else "100644"
        if old == new and old_mode == mode:
            continue
        action: Literal["create", "modify", "delete"] = (
            "create" if old is None else "delete" if new is None else "modify"
        )
        files.append(
            PlannedFile(
                path=path,
                action=action,
                old_mode=old_mode,
                old_blob=old_blob,
                mode=mode,
                content=new,
                old_content=old,
            )
        )
    return files


def _missing_harnesses() -> frozenset[str]:

    return frozenset(n for n in CLI_BINARIES if installed_path(n) is None)


def _harness_warnings(change: Change, missing: frozenset[str]) -> list[str]:

    out = []
    for slot, binding in change.bindings.items():
        name = binding.get("harness")
        if isinstance(name, str) and name in missing:
            out.append(
                f"harness_missing: agent {slot} uses harness {name}, whose CLI "
                f"{binary_name(name)} is not on PATH"
            )
    return out


def _validate(
    state: RepoState, change: Change, missing: frozenset[str], library: LibrarySnapshot
) -> list[Issue]:
    """The planned configuration loads (with the library as it will be) and preflights."""

    if not any(p.startswith(FACTORY_DIR + "/") for p in change.files):
        return []
    after = state.apply(change.files, change.executable)
    if change.moved is not None:
        library = with_item(library, change.moved)
    source = OverlaySource(None, after.factory_files())
    return validate_source(source, after.workflows(), missing, library)


def plan_config(
    command: Command,
    path: Path,
    *,
    type: str,
    name: str,
    commit: bool = False,
    pr: bool = False,
    environ: Mapping[str, str] | None = None,
    **options: Any,
) -> ConfigPlan:
    """The plan of ``config set|remove|move`` for its target; writes nothing."""

    root = _repo_root(path)
    state = read_state(root, "base" if commit else "worktree")
    pr = commit and uses_pr(state.settings, pr)
    target: Target = "pr" if pr else "direct" if commit else "worktree"
    library = library_snapshot(environ)
    if command == "set":
        change = plan_set(state, library, type, name, **options)
    elif command == "move":
        change = plan_move(state, library, type, name)
    else:
        change = plan_remove(state, library, type, name)
    return finish_plan(command, root, target, state, change, library, environ, type=type, name=name)


def finish_plan(
    command: Command,
    root: Path,
    target: Target,
    state: RepoState,
    change: Change,
    library: LibrarySnapshot,
    environ: Mapping[str, str] | None,
    *,
    type: str,
    name: str,
) -> ConfigPlan:
    """The plan of a computed change: blockers, validation, warnings and the digest."""

    files = _planned_files(state, change)
    blockers: list[Blocker] = []
    warnings = list(change.warnings)
    issues: list[Issue] = []
    if files:
        missing = _missing_harnesses()
        if target != "worktree":
            dirty = _dirty(root, files)
            if dirty:
                blockers.append(
                    Blocker(
                        "dirty_paths",
                        "planned paths exist in the working tree with other content: "
                        + ", ".join(dirty)
                        + "; commit them (giro config commit) or move them away first",
                    )
                )
        issues = _validate(state, change, missing, library)
        if issues:
            blockers.append(
                Blocker(
                    "invalid_plan",
                    f"the planned configuration has {len(issues)} problem(s); see the issues",
                )
            )
        store = _store(root) if target != "pr" else None
        try:
            if target == "worktree" and store is not None:
                running = run_blocker(store)
                if running is not None:
                    blockers.insert(0, running)
            elif target == "direct":
                found, notes = direct_blockers(
                    root,
                    remote=state.settings.remote,
                    base=state.base,
                    base_sha=state.base_sha,
                    store=store,
                    check_remote=True,
                )
                blockers += found
                warnings += notes
        except ProviderError as exc:
            raise LibraryStoreError(exc.code, exc.message) from exc
        finally:
            if store is not None:
                store.close()
        warnings += _harness_warnings(change, missing)
    library_plan = _library_plan(type, name, root, change, environ)
    digest = plan_digest(state.base, state.base_sha, files)
    if library_plan is not None:
        digest = combined_digest(digest, library_plan.digest)
    publish = PublishPlan(
        base=state.base,
        base_sha=state.base_sha,
        target=PR if target == "pr" else DIRECT,
        files=tuple(files),
        blockers=tuple(blockers),
        digest=digest,
    )
    return ConfigPlan(
        command=command,
        type=type,
        name=name,
        target=target,
        state=state,
        change=change,
        publish=publish,
        issues=issues,
        warnings=warnings,
        library=library,
        library_plan=library_plan,
    )


def combined_digest(repo: str, library: str | None) -> str:
    """The digest of a move: the repo plan and the library plan together."""
    if library is None:
        return repo
    return hashlib.sha256(f"aifactory-move-v1\0{repo}\0{library}".encode()).hexdigest()


def _library_source(type: str, name: str, root: Path) -> tuple[str, str]:
    """(source, subject) of the library commit of a move."""
    return f"repo {root} {type} {name}", f"library: move {type}/{name} from {root.name}"


def _library_plan(
    type: str,
    name: str,
    root: Path,
    change: Change,
    environ: Mapping[str, str] | None,
) -> LibraryPlan | None:
    """The library write of a move (a dry run of ``store.import_items``)."""
    if not change.library_items:
        return None

    source, subject = _library_source(type, name, root)
    result = import_items(change.library_items, source, subject, dry_run=True, environ=environ)
    change.warnings += result.warnings
    return result.plan
