"""Project and step writes: ``index.md`` flags, add and edit a container."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aifactory.backlog.edit.common import (
    TaskEditError,
    _apply,
    _dedupe,
    _write_checked,
    compose_id,
    load_for_edit,
    serialized_write,
    slugify,
)
from aifactory.backlog.loader import iter_containers, load_backlog
from aifactory.backlog.model import (
    CONTAINER_KEYS,
    HARNESS_KEYS,
    INDEX_FILE,
    Backlog,
    Container,
    Issue,
    node_file,
)
from aifactory.backlog.taskfile import (
    new_index_text,
)
from aifactory.backlog.validate import check_backlog


@dataclass
class ContainerWriteResult:
    action: str
    changed: bool
    path: str
    container: Container
    backlog: Backlog
    issues: list[Issue] = field(default_factory=list)


def _find_container(backlog: Backlog, container_id: str) -> Container | None:
    for container in iter_containers(backlog.containers):
        if container.id == container_id:
            return container
    return None


def set_auto_continue(root: Path, container_id: str, enabled: bool | None) -> ContainerWriteResult:
    """Set (``True``/``False``) or remove (``None``) ``auto_continue`` in a container's index.md.

    Writes the working tree; runs read the backlog from base, so commit it first.
    """
    return _set_container_flag(
        root, container_id, "auto_continue", enabled, "backlog auto-continue"
    )


def set_auto_merge(root: Path, container_id: str, enabled: bool | None) -> ContainerWriteResult:
    """Set (``True``/``False``) or remove (``None``) ``auto_merge`` in a container's index.md.

    Writes the working tree; runs read the backlog from base, so commit it first.
    """
    return _set_container_flag(root, container_id, "auto_merge", enabled, "backlog auto-merge")


@serialized_write
def _set_container_flag(
    root: Path, container_id: str, key: str, enabled: bool | None, command: str
) -> ContainerWriteResult:
    backlog = load_for_edit(root)
    container = _find_container(backlog, container_id)
    if container is None:
        names = " or ".join(backlog.settings.levels[:-1])
        raise TaskEditError("unknown_container", f"no {names} '{container_id}'", id=container_id)
    rel = container.index_path
    if rel is None:
        raise TaskEditError(
            "no_index", f"'{container_id}' has no index.md", id=container_id, path=container.path
        )
    text = (root / rel).read_text(encoding="utf-8")
    if enabled is None:
        new_text = _apply(text, "remove", key)
    else:
        new_text = _apply(text, "set", key, enabled)
    changed = _write_checked(backlog, check_backlog(backlog), {rel: new_text}, command)
    after = load_backlog(root, backlog.settings)
    found = _find_container(after, container_id)
    if found is None:  # pragma: no cover - guarded by validation
        raise TaskEditError("unknown_container", f"'{container_id}' not found after write")
    return ContainerWriteResult(key, changed, rel, found, after, check_backlog(after))


_STR_KEYS = (
    "workflow",
    "harness",
    "model",
    "thinking",
)


def _container_depth(backlog: Backlog, container: Container) -> int:
    return backlog.settings.levels.index(container.level)


def find_container(
    backlog: Backlog, container_id: str, issues: list[Issue] | None = None
) -> Container:
    """The project or step ``container_id``; ``TaskEditError`` when unknown or ambiguous."""
    container = _find_container(backlog, container_id)
    if container is None:
        names = " or ".join(backlog.settings.levels[:-1])
        raise TaskEditError("unknown_container", f"no {names} '{container_id}'", id=container_id)
    if issues is None:
        issues = check_backlog(backlog)
    if any(i.code == "duplicate_id" and i.id == container_id for i in issues):
        raise TaskEditError(
            "duplicate_id",
            f"id '{container_id}' is not unique",
            id=container_id,
            path=container.index_path,
        )
    return container


def _local_code(container_id: str, parent_id: str | None) -> str:
    """The directory code: a step ``M01-S03`` of project ``M01`` lives in ``S03-<slug>``."""
    if parent_id and container_id.startswith(f"{parent_id}-"):
        return container_id[len(parent_id) + 1 :]
    return container_id


def _container_dir(
    backlog: Backlog,
    container_id: str,
    title: str,
    parent_id: str | None = None,
    parent_path: str | None = None,
) -> str:
    """The directory of a new container: ``<parent>/<code>-<slug>``.

    A project (``parent_path`` is ``None``) goes to the first backlog root.
    """
    slug = slugify(title)
    code = _local_code(container_id, parent_id)
    name = f"{code}-{slug}" if slug else code
    if parent_path is None:
        if not backlog.roots:  # pragma: no cover - load_for_edit needs a root
            raise TaskEditError("missing_backlog_dir", "no backlog directory exists")
        parent_path = backlog.roots[0]
    return f"{parent_path}/{name}"


def _container_result(
    backlog: Backlog, action: str, changed: bool, rel: str, container_id: str
) -> ContainerWriteResult:
    after = load_backlog(backlog.root, backlog.settings)
    found = _find_container(after, container_id)
    if found is None:  # pragma: no cover - guarded by validation
        raise TaskEditError("unknown_container", f"'{container_id}' not found after write")
    return ContainerWriteResult(action, changed, rel, found, after, check_backlog(after))


@serialized_write
def add_container(
    root: Path,
    parent: str | None,
    container_id: str,
    title: str,
    *,
    body: str = "",
) -> ContainerWriteResult:
    """Create a project (``parent`` is ``None``) or a step of project ``parent``.

    ``container_id`` is the short code (``S03``); the id is composed with the parent's
    (``M01-S03``, see ``compose_id``). Writes ``<backlog_dir>/<code>-<slug>/index.md`` (a
    step's directory is in its project's directory: ``S03-<slug>``). The id is unique; a
    write that breaks the backlog is rejected with ``backlog_invalid``.
    """
    backlog = load_for_edit(root)
    levels = backlog.settings.levels
    container_id = compose_id(parent, container_id)
    title = title.strip()
    if not title:
        raise TaskEditError("invalid_value", "title must not be empty", id=container_id)
    baseline = check_backlog(backlog)
    parent_node: Container | None = None
    if parent is not None:
        parent_node = find_container(backlog, parent, baseline)
        if _container_depth(backlog, parent_node) + 1 > len(levels) - 2:
            raise TaskEditError(
                "invalid_value",
                f"'{parent}' is a {parent_node.level}; it holds {levels[-1]}s, not {levels[-2]}s",
                id=parent,
            )
    level = levels[0] if parent_node is None else levels[_container_depth(backlog, parent_node) + 1]
    rel_dir = (
        _container_dir(backlog, container_id, title)
        if parent_node is None
        else _container_dir(backlog, container_id, title, parent_node.id, parent_node.path)
    )
    rel = f"{rel_dir}/{INDEX_FILE}"
    if container_id in backlog.by_id:
        other = backlog.by_id[container_id]
        where = node_file(other)
        raise TaskEditError(
            "backlog_invalid",
            f"change rejected: id '{container_id}' exists already",
            exit_code=1,
            issues=[
                Issue(
                    "duplicate_id",
                    f"duplicate id '{container_id}' in {rel} (first defined in {where})",
                    rel,
                    container_id,
                )
            ],
        )
    if (root / rel_dir).exists():
        raise TaskEditError(
            "file_exists", f"directory '{rel_dir}' exists already", path=rel_dir, id=container_id
        )
    text = new_index_text(container_id, title, body)
    changed = _write_checked(backlog, baseline, {rel: text}, f"backlog add {level}")
    return _container_result(backlog, "add", changed, rel, container_id)


def _check_chain(value: object) -> list[str]:
    """``quota_switch``: the canonical names of known harnesses, each at most once."""
    from aifactory.harness import canonical

    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TaskEditError("invalid_value", "'quota_switch' must be a list of harness names")
    chain: list[str] = []
    for item in value:
        try:
            name = canonical(item.strip())
        except ValueError as exc:
            raise TaskEditError("invalid_value", str(exc)) from exc
        if name in chain:
            raise TaskEditError("invalid_value", f"harness {name} is twice in quota_switch")
        chain.append(name)
    return chain


def _check_container_value(key: str, value: object) -> str | list[str] | bool:
    if key in ("harness", "model", "thinking"):
        from aifactory.workflow.task_advice import TaskParameters

        try:
            TaskParameters.model_validate({key: value})
        except ValueError as exc:
            raise TaskEditError("invalid_value", str(exc)) from exc
    if key in _STR_KEYS:
        if not isinstance(value, str) or not value.strip():
            raise TaskEditError("invalid_value", f"'{key}' must be a non-empty string")
        return value.strip()
    if key == "writes":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise TaskEditError("invalid_value", "'writes' must be a list of strings")
        return _dedupe([str(v) for v in value])
    if key == "quota_switch":
        return _check_chain(value)
    if key in ("auto_continue", "auto_heal", "quota_switch_back", "archived"):
        if not isinstance(value, bool):
            raise TaskEditError("invalid_value", f"'{key}' must be true or false")
        return value
    raise TaskEditError(  # pragma: no cover - keys are checked by the caller
        "invalid_value", f"unknown key '{key}'"
    )


@serialized_write
def edit_container(
    root: Path,
    container_id: str,
    *,
    title: str | None = None,
    values: dict[str, object] | None = None,
    clear: list[str] | tuple[str, ...] = (),
) -> ContainerWriteResult:
    """Change the title of a project or step and set (``values``) or remove (``clear``) the
    keys ``CONTAINER_KEYS`` of its ``index.md``. Other keys and the body stay as they are.

    Writes the working tree; runs read the backlog from base, so commit it first.
    """
    values = dict(values or {})
    clear = list(dict.fromkeys(clear))
    for key in [*values, *clear]:
        if key not in CONTAINER_KEYS:
            raise TaskEditError(
                "invalid_value",
                f"unknown key '{key}', allowed: {', '.join(CONTAINER_KEYS)}",
                id=container_id,
            )
    both = [k for k in clear if k in values]
    if both:
        raise TaskEditError(
            "conflicting_options",
            f"'{both[0]}' cannot be set and removed at once",
            id=container_id,
        )
    if title is None and not values and not clear:
        raise TaskEditError("no_changes", "nothing to change, give at least one option")
    checked = {k: _check_container_value(k, v) for k, v in values.items()}
    backlog = load_for_edit(root)
    baseline = check_backlog(backlog)
    container = find_container(backlog, container_id, baseline)
    if container.parent is not None and "archived" in values:
        raise TaskEditError("invalid_value", "only projects can be archived", id=container_id)
    if container.parent is not None and any(k in values for k in HARNESS_KEYS):
        raise TaskEditError("invalid_value", "harness settings are only allowed on a project")
    rel = container.index_path
    if rel is None:
        raise TaskEditError(
            "no_index", f"'{container_id}' has no index.md", id=container_id, path=container.path
        )
    text = (root / rel).read_text(encoding="utf-8")
    if title is not None:
        if not title.strip():
            raise TaskEditError("invalid_value", "title must not be empty", id=container_id)
        text = _apply(text, "set", "title", title.strip())
    for key, value in checked.items():
        text = _apply(text, "set", key, value)
    for key in clear:
        text = _apply(text, "remove", key)
    changed = _write_checked(backlog, baseline, {rel: text}, "backlog edit")
    return _container_result(backlog, "edit", changed, rel, container_id)
