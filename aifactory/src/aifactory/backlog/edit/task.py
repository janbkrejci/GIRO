"""Task writes: add, follow up, edit, complete and link a task."""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from aifactory.backlog.derived import is_human
from aifactory.backlog.edit.common import (
    EDITABLE_STATUSES,
    TaskEditError,
    _apply,
    _dedupe,
    _find_step,
    _next_id,
    _read,
    _write_checked,
    compose_id,
    find_task,
    load_for_edit,
    serialized_write,
    slugify,
    task_revision,
)
from aifactory.backlog.loader import load_backlog
from aifactory.backlog.model import (
    ASSIGNEES,
    Backlog,
    Issue,
    Task,
)
from aifactory.backlog.taskfile import (
    mark_done,
    new_task_text,
    replace_body,
    set_field,
)
from aifactory.backlog.validate import check_backlog


@dataclass
class WriteResult:
    action: str
    changed: bool
    path: str
    task: Task
    backlog: Backlog
    issues: list[Issue] = field(default_factory=list)


def _commit(
    backlog: Backlog,
    baseline: list[Issue],
    changes: dict[str, str],
    action: str,
    target_rel: str,
    task_id: str,
) -> WriteResult:
    changed = _write_checked(backlog, baseline, changes, f"task {action}")
    after = load_backlog(backlog.root, backlog.settings)
    issues = check_backlog(after)
    node = after.by_id.get(task_id)
    if not isinstance(node, Task):  # pragma: no cover - guarded by validation
        raise TaskEditError("unknown_task", f"task '{task_id}' not found after write", id=task_id)
    return WriteResult(action, changed, target_rel, node, after, issues)


@serialized_write
def add_task(
    root: Path,
    step: str,
    title: str,
    *,
    task_id: str | None = None,
    slug: str | None = None,
    workflow: str | None = None,
    writes: list[str] | None = None,
    depends_on: list[str] | None = None,
    related: list[str] | None = None,
    body: str = "",
    parameters: dict[str, object] | None = None,
    assignee: str | None = None,
    follows: str | None = None,
    follow_up_request_id: str | None = None,
    follow_up_request_hash: str | None = None,
) -> WriteResult:
    """Create a new ``todo`` task file in the directory of ``step``.

    `follows` is the id of the done task the new one continues (see ``follow_up_task``).
    """
    backlog = load_for_edit(root)
    container = _find_step(backlog, step)
    title = title.strip()
    if not title:
        raise TaskEditError("invalid_value", "title must not be empty")
    task_id = _next_id(container, step) if task_id is None else compose_id(step, task_id)
    if slug is None:
        slug = slugify(title)
    elif slugify(slug) != slug:
        raise TaskEditError(
            "invalid_value", f"invalid slug '{slug}', expected e.g. '{slugify(slug)}'"
        )
    if workflow is not None and not workflow.strip():
        raise TaskEditError("invalid_value", "workflow must not be empty")
    _check_assignee(assignee)
    if related and task_id in related:
        raise TaskEditError("self_ref", f"task '{task_id}' cannot relate to itself", id=task_id)
    name = f"{task_id}-{slug}.md" if slug else f"{task_id}.md"
    rel = f"{container.path}/{name}"
    if (root / rel).exists():
        raise TaskEditError("file_exists", f"file '{rel}' exists already", path=rel, id=task_id)
    fields: dict[str, object] = {
        "id": task_id,
        "title": title,
        "status": "todo",
        "assignee": assignee,
        "workflow": workflow,
        "depends_on": _dedupe(depends_on or []),
        "related": _dedupe(related) if related is not None else None,
        "follows": follows,
        "writes": _dedupe(writes) if writes is not None else None,
    }
    if parameters:
        _validate_parameters(parameters)
        fields.update({k: v for k, v in parameters.items() if v is not None})
    text = new_task_text(fields, body)
    if follow_up_request_id is not None:
        text = set_field(text, "follow_up_request_id", follow_up_request_id)
        text = set_field(text, "follow_up_request_hash", follow_up_request_hash or "")
    return _commit(backlog, check_backlog(backlog), {rel: text}, "add", rel, task_id)


def follow_up_title(task: Task) -> str:
    """The default title of a task that follows `task`."""
    return f"Navazuje na {task.id}: {task.title}"


def follow_up_commit_message(new_id: str, task_id: str) -> str:
    """The backlog commit of a follow-up that is run at once (CLI and dashboard)."""
    return f"backlog: {new_id} follows {task_id}"


@serialized_write
def follow_up_task(
    root: Path, task_id: str, body: str, *, title: str | None = None, request_id: str | None = None
) -> WriteResult:
    """A new ``todo`` task that follows the done task `task_id` (``factory task follow-up``).

    It goes into the same step, gets ``follows: <task_id>``, the original's own ``writes``
    and ``workflow`` (an inherited value stays inherited) and `body` as its ``## Zadání``.
    The title defaults to ``follow_up_title``. A task that is not done is refused
    (``not_done``).
    """
    backlog = load_for_edit(root)
    request_hash = task_revision(json.dumps([task_id, body, title], ensure_ascii=False))
    if request_id is not None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{16,128}", request_id):
            raise TaskEditError("invalid_value", "invalid follow-up request id")
        from aifactory.backlog.frontmatter import parse_frontmatter

        for existing in backlog.by_id.values():
            if not isinstance(existing, Task):
                continue
            header, _ = parse_frontmatter(_read(root, existing))
            if header.get("follow_up_request_id") == request_id:
                if (
                    existing.follows != task_id
                    or header.get("follow_up_request_hash") != request_hash
                ):
                    raise TaskEditError(
                        "edit_conflict",
                        "follow-up request already used with different content",
                        path=existing.path,
                        id=existing.id,
                    )
                return WriteResult("follow-up", False, existing.path, existing, backlog)
    original = find_task(backlog, task_id)
    if original.status != "done":
        raise TaskEditError(
            "not_done",
            f"task '{task_id}' is {original.status or 'without status'}, only a done task "
            "can be followed up",
            id=task_id,
            path=original.path,
        )
    if not body.strip():
        raise TaskEditError("invalid_value", "body of the follow-up task must not be empty")
    step = original.parent.id
    if step is None:
        raise TaskEditError(
            "unknown_step", f"the step of task '{task_id}' has no id", path=original.parent.path
        )
    workflow = original.own.get("workflow")
    return add_task(
        root,
        step,
        follow_up_title(original) if title is None or not title.strip() else title,
        workflow=str(workflow) if workflow is not None else None,
        writes=list(original.writes) if "writes" in original.own else None,
        body=body,
        follows=original.id,
        follow_up_request_id=request_id,
        follow_up_request_hash=request_hash if request_id else None,
    )


def _check_assignee(assignee: str | None) -> None:
    if assignee is not None and assignee not in ASSIGNEES:
        raise TaskEditError(
            "invalid_value", f"assignee must be one of {', '.join(ASSIGNEES)}, got '{assignee}'"
        )


def human_done_entry() -> str:
    """The ``## Běhy`` line of a task a person marked done."""
    return f"- {datetime.date.today().isoformat()} · hotovo ručně"


@serialized_write
def edit_task(
    root: Path,
    task_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    workflow: str | None = None,
    clear_workflow: bool = False,
    writes: list[str] | None = None,
    clear_writes: bool = False,
    auto_merge: bool | None = None,
    clear_auto_merge: bool = False,
    body: str | None = None,
    depends_on: list[str] | None = None,
    related: list[str] | None = None,
    parameters: dict[str, object] | None = None,
    assignee: str | None = None,
    clear_assignee: bool = False,
    archived: bool | None = None,
    expected_revision: str | None = None,
) -> WriteResult:
    """Change header fields of a task: title, status (todo|cancelled), workflow, writes,
    auto_merge (``clear_auto_merge`` removes the task's own value, so it inherits again),
    assignee, archived. ``status: done`` is set here only for a person's task
    (``assignee: human``)."""
    given = (
        title,
        status,
        workflow,
        writes,
        body,
        depends_on,
        related,
        parameters,
        assignee,
        archived,
    )
    if all(v is None for v in given) and not clear_assignee:
        if not clear_workflow and not clear_writes and auto_merge is None and not clear_auto_merge:
            raise TaskEditError("no_changes", "nothing to change, give at least one option")
    if workflow is not None and clear_workflow:
        raise TaskEditError("conflicting_options", "--workflow and --clear-workflow are exclusive")
    if writes is not None and clear_writes:
        raise TaskEditError("conflicting_options", "--writes and --clear-writes are exclusive")
    if auto_merge is not None and clear_auto_merge:
        raise TaskEditError("conflicting_options", "--auto-merge and inherit are exclusive")
    if assignee is not None and clear_assignee:
        raise TaskEditError("conflicting_options", "--assignee and inherit are exclusive")
    _check_assignee(assignee)
    backlog = load_for_edit(root)
    baseline = check_backlog(backlog)
    task = find_task(backlog, task_id, baseline)
    text = _read(root, task)
    if expected_revision is not None and task_revision(text) != expected_revision:
        raise TaskEditError(
            "edit_conflict",
            "Task se mezitím změnil. Rozepsané změny zůstaly zachované; "
            "porovnej je s aktuální verzí.",
            path=task.path,
            id=task_id,
        )
    if title is not None:
        if not title.strip():
            raise TaskEditError("invalid_value", "title must not be empty", id=task_id)
        text = _apply(text, "set", "title", title.strip())
    if status == "done" and task.status != "done":
        if not is_human(task):
            raise TaskEditError(
                "invalid_status",
                "'done' is set only by PR approval; a person's task (assignee: human) "
                "can be marked done",
                id=task_id,
            )
        text = mark_done(text, human_done_entry())
    elif status is not None:
        if status not in EDITABLE_STATUSES:
            raise TaskEditError(
                "invalid_status",
                f"status must be one of {', '.join(EDITABLE_STATUSES)}; "
                "'done' is set only by PR approval",
                id=task_id,
            )
        if task.status == "done" and status != "done":
            raise TaskEditError(
                "invalid_status",
                "task is done; its status changes only through PR approval",
                id=task_id,
            )
        text = _apply(text, "set", "status", status)
    if archived is not None:
        if not isinstance(archived, bool):
            raise TaskEditError("invalid_value", "'archived' must be true or false", id=task_id)
        if (status or task.status) not in ("done", "cancelled"):
            raise TaskEditError(
                "invalid_value", "only done or cancelled tasks can be archived", id=task_id
            )
        default = (status or task.status) == "cancelled"
        text = _apply(text, "remove" if archived == default else "set", "archived", archived)
    if workflow is not None:
        if not workflow.strip():
            raise TaskEditError("invalid_value", "workflow must not be empty", id=task_id)
        text = _apply(text, "set", "workflow", workflow.strip())
    if clear_workflow:
        text = _apply(text, "remove", "workflow")
    if writes is not None:
        text = _apply(text, "set", "writes", _dedupe(writes))
    if clear_writes:
        text = _apply(text, "remove", "writes")
    if auto_merge is not None:
        text = _apply(text, "set", "auto_merge", auto_merge)
    if clear_auto_merge:
        text = _apply(text, "remove", "auto_merge")
    if assignee is not None:
        text = _apply(text, "set", "assignee", assignee)
    if clear_assignee:
        text = _apply(text, "remove", "assignee")
    for key, values in (("depends_on", depends_on), ("related", related)):
        if values is not None:
            if task_id in values:
                raise TaskEditError("self_ref", "task cannot reference itself", id=task_id)
            text = _apply(text, "set", key, _dedupe(values))
    if parameters is not None:
        _validate_parameters(parameters)
        for key, value in parameters.items():
            assert value is None or isinstance(value, (str, list, bool, int))
            text = _apply(text, "remove" if value is None else "set", key, value)
    if body is not None:
        text = replace_body(text, body)
    return _commit(backlog, baseline, {task.path: text}, "edit", task.path, task_id)


@serialized_write
def complete_task(root: Path, task_id: str, entry: str) -> WriteResult:
    """``status: done`` and `entry` in ``## Běhy`` for a task that ends without a PR.

    For a run that found nothing to change and for a task done by a person. A cancelled
    task is not completed; a done one comes back unchanged.
    """
    backlog = load_for_edit(root)
    baseline = check_backlog(backlog)
    task = find_task(backlog, task_id, baseline)
    if task.status == "cancelled":
        raise TaskEditError("invalid_status", "task is cancelled", id=task_id)
    text = mark_done(_read(root, task), entry)
    return _commit(backlog, baseline, {task.path: text}, "done", task.path, task_id)


@serialized_write
def link_task(
    root: Path,
    task_id: str,
    *,
    depends_on: list[str] | None = None,
    related: list[str] | None = None,
    remove: bool = False,
) -> WriteResult:
    """Add (or with ``remove`` drop) ``depends_on`` / ``related`` references of a task."""
    depends_on = _dedupe(depends_on or [])
    related = _dedupe(related or [])
    if not depends_on and not related:
        raise TaskEditError("no_changes", "give at least one --depends-on or --related id")
    if task_id in depends_on or task_id in related:
        raise TaskEditError("self_ref", f"task '{task_id}' cannot refer to itself", id=task_id)
    backlog = load_for_edit(root)
    baseline = check_backlog(backlog)
    task = find_task(backlog, task_id, baseline)
    text = _read(root, task)
    for key, refs, current in (
        ("depends_on", depends_on, task.depends_on),
        ("related", related, task.related),
    ):
        if not refs:
            continue
        if remove:
            new = [r for r in current if r not in refs]
        else:
            new = current + [r for r in refs if r not in current]
        if new == current:
            continue
        if not new and key == "related":
            text = _apply(text, "remove", key)
        else:
            text = _apply(text, "set", key, new)
    return _commit(backlog, baseline, {task.path: text}, "link", task.path, task_id)


def _validate_parameters(parameters: dict[str, object]) -> None:
    from pydantic import ValidationError

    from aifactory.workflow.task_advice import TaskParameters

    try:
        TaskParameters.model_validate(parameters)
    except ValidationError as exc:
        raise TaskEditError("invalid_value", "invalid task parameters") from exc
