"""``add_batch``: several new tasks, optionally in a new step and project, at once."""

from __future__ import annotations

from dataclasses import dataclass, field

from aifactory.backlog.edit.common import (
    Root,
    TaskEditError,
    _dedupe,
    _find_step,
    _write_checked,
    compose_id,
    files_of,
    load_for_edit,
    serialized_write,
    slugify,
)
from aifactory.backlog.edit.container import _container_depth, _container_dir, find_container
from aifactory.backlog.model import (
    INDEX_FILE,
    Backlog,
    Issue,
    node_file,
)
from aifactory.backlog.taskfile import (
    new_index_text,
    new_task_text,
)
from aifactory.backlog.validate import check_backlog


@dataclass
class NewTask:
    """One task of a batch (``add_batch``); ``depends_on`` may name codes of the batch."""

    code: str
    title: str
    body: str = ""
    workflow: str | None = None
    writes: list[str] | None = None
    depends_on: list[str] = field(default_factory=list)


@dataclass
class BatchTask:
    id: str
    title: str
    body: str
    workflow: str | None
    writes: list[str] | None
    depends_on: list[str]
    path: str


@dataclass
class BatchResult:
    """A planned (``written`` False) or written batch of one step and its new tasks.

    The ``project_*`` fields describe the step's project (new or existing); they are
    empty in a backlog without a project level.
    """

    step_id: str
    step_title: str
    step_body: str
    step_path: str
    new_step: bool
    tasks: list[BatchTask]
    files: list[str]
    written: bool
    project_id: str = ""
    project_title: str = ""
    project_body: str = ""
    project_path: str = ""
    new_project: bool = False


def _new_container_problems(
    root: Root, backlog: Backlog, container_id: str, title: str, rel_dir: str, level: str
) -> list[Issue]:
    """Problems of a new container of a batch: empty title, taken id, existing directory."""
    problems: list[Issue] = []
    if not title:
        problems.append(Issue("invalid_value", f"{level} title must not be empty", ""))
    if container_id in backlog.by_id:
        where = node_file(backlog.by_id[container_id])
        problems.append(
            Issue(
                "duplicate_id", f"id '{container_id}' exists already ({where})", where, container_id
            )
        )
    if files_of(root).exists(rel_dir):
        problems.append(
            Issue("file_exists", f"directory '{rel_dir}' exists already", rel_dir, container_id)
        )
    return problems


@serialized_write
def add_batch(
    root: Root,
    tasks: list[NewTask],
    *,
    step: str | None = None,
    project: str | None = None,
    step_code: str | None = None,
    step_title: str | None = None,
    step_body: str = "",
    project_code: str | None = None,
    project_title: str | None = None,
    project_body: str = "",
    write: bool = False,
) -> BatchResult:
    """Add ``tasks`` to the existing ``step``, to a new step ``step_code`` of ``project``,
    or to a new step ``step_code`` of a new project ``project_code``.

    Every problem is collected first and raised together as ``backlog_invalid`` (codes as
    in ``factory backlog check`` plus ``invalid_id``, ``invalid_value``, ``self_ref``,
    ``duplicate_id``, ``file_exists``); then the whole batch is validated on a copy of the
    backlog like ``add_task``. Without ``write`` nothing is written; nothing is committed.
    """
    if project_code is not None:
        if step is not None or project is not None:
            raise TaskEditError(
                "conflicting_options", "a new project excludes an existing step or project"
            )
    elif (step is None) == (project is None):
        raise TaskEditError("conflicting_options", "give either a step or a project")
    backlog = load_for_edit(root)
    baseline = check_backlog(backlog)
    levels = backlog.settings.levels
    problems: list[Issue] = []
    new_step = step is None
    new_project = project_code is not None
    project_id = p_title = p_body = p_dir = ""
    if step is not None:
        container = _find_step(backlog, step)
        step_id = container.id or step
        title = container.title or ""
        body = container.body
        rel_dir = container.path
        existing = {t.id for t in container.tasks}
        holder = container.parent
        if holder is not None:
            project_id = holder.id or ""
            p_title, p_body, p_dir = holder.title or "", holder.body.strip(), holder.path
    else:
        if project_code is not None:
            if len(levels) != 3:
                raise TaskEditError(
                    "invalid_value",
                    f"a new {levels[0]} needs the levels project, step, task "
                    f"(this backlog has {', '.join(levels)})",
                    id=project_code,
                )
            project_id = compose_id(None, project_code)
            p_title = (project_title or "").strip()
            p_body = project_body.strip()
            p_dir = _container_dir(backlog, project_id, p_title)
            problems += _new_container_problems(
                root, backlog, project_id, p_title, p_dir, levels[0]
            )
        else:
            assert project is not None
            parent = find_container(backlog, project, baseline)
            if _container_depth(backlog, parent) != len(levels) - 3:
                raise TaskEditError(
                    "invalid_value", f"'{project}' does not hold {levels[-2]}s", id=project
                )
            project_id = parent.id or ""
            p_title, p_body, p_dir = parent.title or "", parent.body.strip(), parent.path
        step_id = compose_id(project_id, step_code or "")
        title = (step_title or "").strip()
        body = step_body
        rel_dir = _container_dir(backlog, step_id, title, project_id, p_dir)
        problems += _new_container_problems(root, backlog, step_id, title, rel_dir, levels[-2])
        existing = set()
    ids: dict[str, str] = {}
    for task in tasks:
        try:
            task_id = compose_id(step_id, task.code)
        except TaskEditError as exc:
            problems.append(Issue(exc.code, exc.message, "", task.code))
            continue
        if task_id in ids.values() or task_id in backlog.by_id or task_id in existing:
            problems.append(
                Issue("duplicate_id", f"task id '{task_id}' is not unique", "", task_id)
            )
        ids[task.code] = task_id
        ids.setdefault(task_id, task_id)
    planned: list[BatchTask] = []
    changes: dict[str, str] = {}
    if new_project:
        changes[f"{p_dir}/{INDEX_FILE}"] = new_index_text(project_id, p_title, project_body)
    if new_step:
        changes[f"{rel_dir}/{INDEX_FILE}"] = new_index_text(step_id, title, body)
    for task in tasks:
        if task.code not in ids:
            continue
        task_id = ids[task.code]
        task_title = task.title.strip()
        if not task_title:
            problems.append(Issue("invalid_value", "title must not be empty", "", task_id))
        if task.workflow is not None and not task.workflow.strip():
            problems.append(Issue("invalid_value", "workflow must not be empty", "", task_id))
        depends = _dedupe([ids.get(ref, ref) for ref in task.depends_on])
        if task_id in depends:
            problems.append(
                Issue("self_ref", f"task '{task_id}' cannot depend on itself", "", task_id)
            )
        slug = slugify(task_title)
        rel = f"{rel_dir}/{task_id}-{slug}.md" if slug else f"{rel_dir}/{task_id}.md"
        if files_of(root).exists(rel):
            problems.append(Issue("file_exists", f"file '{rel}' exists already", rel, task_id))
        writes = _dedupe(task.writes) if task.writes is not None else None
        fields: dict[str, object] = {
            "id": task_id,
            "title": task_title,
            "status": "todo",
            "workflow": task.workflow,
            "depends_on": depends,
            "writes": writes,
        }
        changes[rel] = new_task_text(fields, task.body)
        planned.append(
            BatchTask(task_id, task_title, task.body.strip(), task.workflow, writes, depends, rel)
        )
    if not tasks:
        problems.append(Issue("invalid_value", "the batch has no task", "", step_id))
    if not problems:
        for rel in changes:
            try:
                files_of(root).guard(backlog, rel)
            except TaskEditError as exc:
                problems.append(Issue(exc.code, exc.message, rel))
    if not problems:
        problems = files_of(root).new_issues(backlog, baseline, changes)
    if problems:
        raise TaskEditError(
            "backlog_invalid",
            f"change rejected: {len(problems)} problem(s)",
            exit_code=1,
            issues=problems,
        )
    if write:
        _write_checked(files_of(root), backlog, baseline, changes, "backlog propose")
    return BatchResult(
        step_id=step_id,
        step_title=title,
        step_body=body.strip(),
        step_path=rel_dir,
        new_step=new_step,
        tasks=planned,
        files=list(changes),
        written=write,
        project_id=project_id,
        project_title=p_title,
        project_body=p_body,
        project_path=p_dir,
        new_project=new_project,
    )
