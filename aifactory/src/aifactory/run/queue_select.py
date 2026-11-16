"""Selection for auto-continue: candidates, priorities, slots and occupancy.

``select_next`` picks the next task of a chain, ``select_ready`` the ready tasks of the whole
repository, ``priority_order`` puts blocked tasks and their blockers first and
``Occupancy`` and ``Slots`` count what runs and which open PRs hold slots and paths. The rules
are described in ``run.chain``.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from aifactory import failure as failures
from aifactory.backlog import (
    Backlog,
    Task,
    has_workflow,
    is_human,
    iter_tasks,
    unmet,
)
from aifactory.backlog.derived import ancestors, descendant_tasks
from aifactory.backlog.followup_ids import follow_up_marker
from aifactory.run import task as task_mod
from aifactory.run.policy import enabled
from aifactory.run.stack import stacked_on, unmet_ids
from aifactory.runstore.rows import (
    Priority,
    QueuePrefs,
    TaskPrRow,
    TaskRunRow,
)
from aifactory.runstore.store import TaskRunStore
from aifactory.scope import effective_task_writes, is_wide, overlaps

SKIP_REASONS: tuple[str, ...] = (
    "needs_environment",
    "human_task",
    "no_workflow",
    "waits_on_pr",
    "blocked",
    "in_review",
    "merged_outside",
    "running",
    "cannot_start",
    "writes_overlap",
    "exclusive",
    "excluded",
    "no_slot",
)


EXCLUDED_DETAIL = "vyloučeno z auto continue v kanbanu"


NO_WORKFLOW_DETAIL = "bez workflow"


HUMAN_TASK_DETAIL = "pro člověka"


_HARD_REASONS = ("unknown", "cancelled", "empty")


@dataclass
class Skip:
    """A task the chain did not start, and why."""

    task_id: str
    reason: str  # one of SKIP_REASONS
    detail: str
    waits_on: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "reason": self.reason,
            "detail": self.detail,
            "waits_on": list(self.waits_on),
        }


@dataclass(frozen=True)
class Slots:
    """Who holds the repository's parallel slots: live runs and PRs waiting for review.

    ``running``: tasks with a live run (or a started chain member). ``in_review``: tasks
    with an open, unmerged PR and no live run. A task counts once, also when it has a
    live run (a resolve, a retry) and an open PR at the same time. A PR that a live run or
    another open PR is stacked on (``stacked_prs``) holds no slot of its own: the stack
    takes one, and the first task stacked on a PR takes over that PR's slot (``yields_to``).
    """

    running: tuple[str, ...] = ()
    in_review: tuple[str, ...] = ()

    @classmethod
    def count(cls, running: Iterable[str], open_pr_tasks: Iterable[str]) -> Slots:
        live = tuple(dict.fromkeys(running))
        review = tuple(t for t in dict.fromkeys(open_pr_tasks) if t not in live)
        return cls(live, review)

    @classmethod
    def of(cls, store: TaskRunStore, members: Iterable[str] = ()) -> Slots:
        """The slots taken in `store`'s repository, plus the started `members` of a chain."""
        runs = store.live_runs()
        prs = store.open_prs()
        live = [*members, *(row.task_id for row in runs)]
        bases = {row.stacked_on for row in runs if row.stacked_on}
        bases |= {base for pr in prs if (base := stacked_on(store, pr.branch))}
        return cls.count(live, (pr.task_id for pr in prs if pr.branch not in bases))

    def yields_to(self, dependencies: Iterable[str]) -> bool:
        """A task stacked on one of these PRs takes over its slot (nobody stacks on it yet)."""
        return any(dep in self.in_review for dep in dependencies)

    @property
    def taken(self) -> int:
        return len(self.running) + len(self.in_review)

    def free(self, limit: int) -> int:
        return max(0, limit - self.taken)

    def skip(self, task_id: str) -> Skip:
        """`task_id` did not start: no free slot, PRs waiting for review hold some (``no_slot``)."""
        detail = f"žádný volný slot: PR čekající na review ({', '.join(self.in_review)})"
        if self.running:
            detail += f", běží ({', '.join(self.running)})"
        return Skip(task_id, "no_slot", detail, list(self.in_review))


@dataclass
class Selection:
    next: Task | None
    skipped: list[Skip]


def auto_continue_enabled(repo: Path) -> bool:
    """Live repository policy; legacy backlog flags never authorize a start."""
    return enabled(repo)


def auto_merge_enabled(repo: Path) -> bool:
    """Fresh repository-local policy, independent of backlog and auto continue."""
    from aifactory.run.policy import merge_enabled

    return merge_enabled(repo)


def candidates(backlog: Backlog, after: Task, order: Sequence[str] = ()) -> list[Task]:
    """`todo` tasks: own project's `order`, same step, then the rest of the project.

    The manual order wins over "same step first"; ids in `order` outside the project
    (or not `todo`) are ignored.
    """
    step = after.parent
    project = ancestors(after)[-1]
    pool: list[Task] = []
    seen = {after.id}
    for task in [*descendant_tasks(step), *descendant_tasks(project)]:
        if task.id in seen:
            continue
        seen.add(task.id)
        if task.status == "todo":
            pool.append(task)
    rank = {task_id: i for i, task_id in enumerate(order)}
    ranked = sorted((t for t in pool if t.id in rank), key=lambda t: rank[t.id])
    same_step = [t for t in pool if t.id not in rank and t.parent is step]
    rest = [t for t in pool if t.id not in rank and t.parent is not step]
    return [*ranked, *same_step, *rest]


def select_next(
    backlog: Backlog,
    after: Task,
    *,
    open_pr: Callable[[str], TaskPrRow | None],
    running: Callable[[str], TaskRunRow | None],
    latest_pr: Callable[[str], TaskPrRow | None] | None = None,
    exclude: Collection[str] = frozenset(),
    conflict: Callable[[Task], Skip | None] | None = None,
    require_auto_continue: bool = False,
    prefs: QueuePrefs | None = None,
    prefer: Sequence[str] = (),
    stack: Stacker | None = None,
) -> Selection:
    """The first startable candidate after `after`, and every candidate that was skipped.

    Candidates stay in `after`'s project, including queued and priority tasks.

    ``conflict`` (parallel chains, ``Occupancy``) is asked only about a candidate that
    could start otherwise, until one is chosen; a ``Skip`` it returns is recorded and the
    next candidate is tried. ``require_auto_continue`` is retained for compatibility
    and no longer filters task metadata.
    ``prefs`` (the kanban's queue, ``TaskRunStore.queue_prefs``) orders the candidates
    and excludes tasks: an excluded task is skipped as ``excluded``, with ``--auto`` too.
    ``prefer`` (the blockers of a blocked `after`, ``blockers_of``) goes before the
    kanban's queue, after the priority records (``priority_order``); like the queue, ids
    outside `after`'s project are ignored.
    """
    prefs = prefs or QueuePrefs()
    first = priority_order(backlog, prefs.priority)
    return _select(
        candidates(backlog, after, list(dict.fromkeys([*first, *prefer, *prefs.order]))),
        backlog,
        open_pr=open_pr,
        latest_pr=latest_pr,
        running=running,
        exclude=exclude,
        conflict=conflict,
        require_auto_continue=require_auto_continue,
        prefs=prefs,
        stack=stack,
    )


def blockers_of(backlog: Backlog, blocked: Task, ids: Sequence[str]) -> list[str]:
    """Of `ids` (``task.run_blockers``), the follow-ups the factory filed for `blocked`.

    Only tasks of `blocked`'s project that carry its follow-up marker count: the blockers
    that came up during its run. Other ids (an existing task in ``blocked_by``, a
    placeholder of a follow-up that was not filed) are left to the usual selection.
    """
    project = ancestors(blocked)[-1]
    marker = follow_up_marker(blocked.id)
    found: list[str] = []
    for task_id in ids:
        node = backlog.by_id.get(task_id)
        if (
            isinstance(node, Task)
            and marker in node.body
            and ancestors(node)[-1] is project
            and task_id not in found
        ):
            found.append(task_id)
    return found


def _todo(backlog: Backlog, task_id: str) -> Task | None:
    node = backlog.by_id.get(task_id)
    return node if isinstance(node, Task) and node.status == "todo" else None


def _waits(backlog: Backlog, task: Task) -> list[str]:
    """The ids `task` waits for (its unmet dependencies, containers as their tasks)."""
    ids: list[str] = []
    for item in unmet(backlog, task):
        for dep in item.missing or [item.id]:
            if dep not in ids:
                ids.append(dep)
    return ids


def priority_order(backlog: Backlog, priority: Sequence[Priority]) -> list[str]:
    """The task ids the priority records put before the kanban's queue, in this order.

    First every blocked task (``todo``) whose blockers are all finished (no blocker is a
    ``todo`` task), oldest record first; then the unfinished blockers of the other blocked
    tasks, each after its own unfinished blockers (its priority record and its unmet
    dependencies), and each blocked task right after its blockers (the selection's checks
    keep it waiting until they are done or it can stack on their PRs). Records of tasks no
    longer ``todo`` count for nothing.
    """
    return list(priority_map(backlog, priority))


def priority_map(backlog: Backlog, priority: Sequence[Priority]) -> dict[str, list[str]]:
    """``priority_order`` with, for each id, the blocked tasks whose records give it priority."""
    records = {p.task_id: p for p in priority if _todo(backlog, p.task_id) is not None}
    found: dict[str, list[str]] = {}
    for task_id, rec in records.items():
        if not any(_todo(backlog, b) is not None for b in rec.blockers):
            found[task_id] = [task_id]
    blockers: dict[str, list[str]] = {}

    def visit(task_id: str, owner: str, path: frozenset[str], seen: set[str]) -> None:
        if task_id in path or task_id in seen:
            return
        task = _todo(backlog, task_id)
        if task is None:
            return
        seen.add(task_id)
        rec = records.get(task_id)
        inner = path | {task_id}
        for dep in [*(rec.blockers if rec is not None else ()), *_waits(backlog, task)]:
            visit(dep, owner, inner, seen)
        owners = blockers.setdefault(task_id, [])  # after its own blockers
        if owner not in owners:
            owners.append(owner)

    for task_id, rec in records.items():
        if task_id in found:
            continue
        seen: set[str] = set()
        for blocker in rec.blockers:
            visit(blocker, task_id, frozenset({task_id}), seen)
        # right after its last blocker: it starts once they are done (or stacks on their PRs)
        owners = blockers.setdefault(task_id, [])
        if task_id not in owners:
            owners.append(task_id)
    for task_id, owners in blockers.items():
        if task_id in found:
            found[task_id].extend(o for o in owners if o not in found[task_id])
        else:
            found[task_id] = owners
    return found


def _may_requeue(backlog: Backlog, task: Task, ids: Sequence[str]) -> bool:
    """A blocked task may start again in its chain: every blocker is a backlog task and the
    task waits for the unfinished ones in base (its ``depends_on``), so it cannot start
    before them."""
    if not ids or not all(isinstance(backlog.by_id.get(i), Task) for i in ids):
        return False
    waits = set(_waits(backlog, task))
    return bool(waits) and all(i in waits for i in ids if _todo(backlog, i) is not None)


def _environment_skip(result: task_mod.TaskRunResult) -> Skip:
    detail = result.run.failure_detail_dict() or {}
    agent = detail.get("agent") or "agent"
    summary = detail.get("summary") or "chybí prostředí pro práci"
    return Skip(result.run.task_id, failures.NEEDS_ENVIRONMENT, f"{agent}: {summary}")


def _blocked_skip(task: Task, ids: Sequence[str]) -> Skip:
    return Skip(task.id, "blocked", f"během běhu zablokován: {', '.join(ids)}", list(ids))


def repo_candidates(backlog: Backlog, order: Sequence[str] = ()) -> list[Task]:
    """Every `todo` task of the backlog: first those in `order` (the kanban's manual
    queue), then the rest in backlog order (project by project)."""
    pool = [task for task in iter_tasks(backlog) if task.status == "todo"]
    rank = {task_id: i for i, task_id in enumerate(order)}
    ranked = sorted((t for t in pool if t.id in rank), key=lambda t: rank[t.id])
    return [*ranked, *(t for t in pool if t.id not in rank)]


def select_ready(
    backlog: Backlog,
    *,
    open_pr: Callable[[str], TaskPrRow | None],
    running: Callable[[str], TaskRunRow | None],
    latest_pr: Callable[[str], TaskPrRow | None] | None = None,
    exclude: Collection[str] = frozenset(),
    conflict: Callable[[Task], Skip | None] | None = None,
    prefs: QueuePrefs | None = None,
    stack: Stacker | None = None,
) -> Selection:
    """The first startable task of the whole backlog (the Overview's "Spustit továrnu").

    The same checks as ``select_next`` over ``repo_candidates``; ``auto_continue`` is not
    required (a start by hand), a task excluded in the kanban is skipped as ``excluded``.
    """
    prefs = prefs or QueuePrefs()
    first = priority_order(backlog, prefs.priority)
    return _select(
        repo_candidates(backlog, list(dict.fromkeys([*first, *prefs.order]))),
        backlog,
        open_pr=open_pr,
        latest_pr=latest_pr,
        running=running,
        exclude=exclude,
        conflict=conflict,
        require_auto_continue=False,
        prefs=prefs,
        stack=stack,
    )


def _select(
    pool: Sequence[Task],
    backlog: Backlog,
    *,
    open_pr: Callable[[str], TaskPrRow | None],
    running: Callable[[str], TaskRunRow | None],
    latest_pr: Callable[[str], TaskPrRow | None] | None = None,
    exclude: Collection[str],
    conflict: Callable[[Task], Skip | None] | None,
    require_auto_continue: bool,
    prefs: QueuePrefs,
    stack: Stacker | None = None,
) -> Selection:
    """The first startable task of `pool` and every task of it that was skipped.

    With `stack` (``stacked_prs``) a task whose dependencies all wait in open PRs that
    one branch holds starts stacked on it instead of waiting (``waits_on_pr``).
    """
    chosen: Task | None = None
    skipped: list[Skip] = []
    for task in pool:
        if task.id in exclude:
            continue
        if task.id in prefs.excluded:
            skipped.append(Skip(task.id, "excluded", EXCLUDED_DETAIL))
            continue
        if is_human(task):
            skipped.append(Skip(task.id, "human_task", HUMAN_TASK_DETAIL))
            continue
        if not has_workflow(task):
            skipped.append(Skip(task.id, "no_workflow", NO_WORKFLOW_DETAIL))
            continue
        busy = running(task.id)
        if busy is not None:
            skipped.append(Skip(task.id, "running", f"run {busy.run_id} on {busy.branch}"))
            continue
        newest = latest_pr(task.id) if latest_pr is not None else None
        if newest is not None and newest.state == "merged":
            skipped.append(
                Skip(
                    task.id,
                    "merged_outside",
                    f"PR {newest.url} sloučen; spusťte factory backlog sync",
                )
            )
            continue
        own = open_pr(task.id)
        if own is not None:
            skipped.append(Skip(task.id, "in_review", f"PR {own.url}"))
            continue
        missing = unmet(backlog, task)
        if missing:
            waits: list[str] = []
            hard = False
            for u in missing:
                ids = list(u.missing) if u.missing else [u.id]
                hard = hard or u.reason in _HARD_REASONS
                waits.extend(i for i in ids if i not in waits)
            prs = {i: open_pr(i) for i in waits}
            found = {i: pr for i, pr in prs.items() if pr is not None}
            stackable = not hard and len(found) == len(prs) and stack is not None and stack(found)
            if not stackable:
                if hard or any(pr is None for pr in prs.values()):
                    detail = ", ".join(
                        f"{u.id} ({u.reason}"
                        + (f": {', '.join(u.missing)}" if u.missing and u.missing != [u.id] else "")
                        + ")"
                        for u in missing
                    )
                    skipped.append(Skip(task.id, "blocked", detail, waits))
                else:
                    detail = ", ".join(f"{i} (PR {pr.url})" for i, pr in prs.items() if pr)
                    skipped.append(Skip(task.id, "waits_on_pr", detail, waits))
                continue
        if chosen is None:
            clash = conflict(task) if conflict is not None else None
            if clash is not None:
                skipped.append(clash)
                continue
            chosen = task
    return Selection(chosen, skipped)


_MAX_PATHS = 5


UNKNOWN_FILES = "soubory PR nelze zjistit"


def _paths_text(paths: list[str]) -> str:
    shown = ", ".join(paths[:_MAX_PATHS])
    return shown + (", …" if len(paths) > _MAX_PATHS else "")


def _writes_text(writes: Collection[str]) -> str:
    return ", ".join(w or "(celé repo)" for w in writes)


@dataclass
class Occupancy:
    """What runs and what waits in open PRs: the ``conflict`` of a parallel chain's selection.

    ``runs``: every live run with its task's effective ``writes`` (``("",)``, the whole
    repo, when the task is not in the backlog). ``prs``: every open PR with the files it
    changes (``None`` when unknown: then its task's ``writes``, else the whole repo).
    """

    runs: list[tuple[TaskRunRow, tuple[str, ...]]]
    prs: list[tuple[TaskPrRow, list[str], bool]]  # (pr, paths, files known)
    root: Path | None = None
    # the backlog the selection reads: a task never waits for the PRs of its dependencies,
    # it waits for them as dependencies or stacks on them (``stacked_prs``)
    backlog: Backlog | None = None

    @classmethod
    def build(
        cls,
        backlog: Backlog,
        store: TaskRunStore,
        root: Path,
        pr_files: Callable[[TaskPrRow], list[str] | None],
    ) -> Occupancy:
        def writes_of(task_id: str) -> tuple[str, ...] | None:
            node = backlog.by_id.get(task_id)
            if not isinstance(node, Task):
                return None
            return effective_task_writes(node) or None

        runs = [(row, writes_of(row.task_id) or ("",)) for row in store.live_runs()]
        prs: list[tuple[TaskPrRow, list[str], bool]] = []
        for pr in store.open_prs():
            files = pr_files(pr)
            if files is not None:
                prs.append((pr, files, True))
            else:
                prs.append((pr, list(writes_of(pr.task_id) or ("",)), False))
        return cls(runs, prs, root, backlog)

    def _wide(self, writes: Collection[str]) -> list[str]:
        return is_wide(writes, self.root)

    def __call__(self, task: Task) -> Skip | None:
        writes = effective_task_writes(task)
        if not writes:
            return None  # run_task refuses it (no_writes): reported as cannot_start
        wide = self._wide(writes)
        if wide and self.runs:
            running = ", ".join(f"{row.task_id} {row.run_id}" for row, _ in self.runs)
            return Skip(
                task.id,
                "exclusive",
                f"writes {_writes_text(wide)} pokrývají celý balíček/repo; běží jen samostatně "
                f"(běží: {running})",
                [row.task_id for row, _ in self.runs],
            )
        for row, run_writes in self.runs:
            run_wide = self._wide(run_writes)
            if run_wide:
                return Skip(
                    task.id,
                    "exclusive",
                    f"běží {row.task_id} ({row.run_id}) se širokými writes "
                    f"{_writes_text(run_wide)}; souběžně nic jiného",
                    [row.task_id],
                )
        details: list[str] = []
        waits: list[str] = []
        for row, run_writes in self.runs:
            hit = overlaps(writes, run_writes)
            if hit:
                details.append(f"běh {row.run_id} ({row.task_id}): {_paths_text(hit)}")
                waits.append(row.task_id)
        deps = set(unmet_ids(self.backlog, task) or ()) if self.backlog is not None else set()
        for pr, paths, known in self.prs:
            if pr.task_id == task.id or pr.task_id in deps:
                continue
            hit = overlaps(writes, paths)
            if hit:
                text = _paths_text(hit) if known else f"{UNKNOWN_FILES}, writes {_paths_text(hit)}"
                details.append(f"PR {pr.url} ({pr.task_id}): {text}")
                if pr.task_id not in waits:
                    waits.append(pr.task_id)
        if details:
            return Skip(task.id, "writes_overlap", "; ".join(details), waits)
        return None


Stacker = Callable[[dict[str, TaskPrRow]], bool]
