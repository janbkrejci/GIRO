"""The ``task_runs``, ``task_prs``, ``sync_prs`` and ``task_chains`` tables in the trace DB.

``task_operations`` records a task, action, process and start time before claim or
while publishing again. Launchers remove completed operations; ``live_operations``
reaps dead local processes. Operations are display evidence, not run claims or slots.

``task_runs`` has one row per run and is the lock between runs; ``task_prs`` has
one row per branch with a pull request (``open``, ``merged`` or ``closed``);
``sync_prs`` has one row per ``backlog sync`` pull request (it belongs to no task).
``task_prs.merged_by`` says who merged the PR (``auto-merge`` or ``operator``);
``task_prs.auto_merge_error`` why auto-merge left the PR open. ``task_runs.started_by``
says who started a run: ``manual`` (``factory task run|return|resolve``, the dashboard),
``auto-continue`` (a chain), ``auto-resolve`` (auto-merge resolving a conflict) or
``auto-ci-fix`` (approve repairing the PR's red CI checks); NULL for runs recorded before.
``task_runs.library_commit`` is the library commit the run read its inherited agents,
workflows and skills from (NULL: the seed, or recorded before). Older trace DBs get these
columns added when the store opens them, and ``task_runs.archived`` too.

``task_runs.failure_kind`` is the typed kind of a run that did not succeed (one of
``aifactory.failure.FAILURE_KINDS``) and ``task_runs.failure_detail`` its facts as a JSON
object; ``error`` stays the free text. ``finish`` records them, ``mark_stopped`` sets
``stopped``, the reaping of a dead run ``process_gone``, ``reclaim`` clears them. A
failed, stopped or aborted run recorded before the columns existed reads as ``other``,
or ``blocked`` when its error starts with ``blocked: `` (``TaskRunRow.shown_failure_kind``).

``task_chains`` has one row per auto-continue chain (``queue.run_chain``): its runs
in start order, the tasks the last selection skipped and why, the tasks that ran
alone (wide writes), its automatic actions after a failure (``events``, ``run.heal``),
and the chain's state (``running``, ``finished`` with its
``stop``, or ``aborted`` when the chain's process is gone). ``dismiss_chain`` erases an
ended chain (its runs stay).

``task_queue`` holds the operator's queue preferences from the kanban: the order of
ready tasks auto-continue takes them in (``rank``, NULL without a manual order) and
the tasks auto-continue never starts (``excluded``), plus archived tasks (``archived``).
They are operating preferences, not task content, so they live here and need no backlog
commit (``queue_prefs``, ``set_queue_order``, ``set_excluded``, ``set_archived``).

``task_priority`` holds the tasks that ended blocked during a run and their blockers
(``failure_kind`` ``blocked``, including existing tasks from ``blocked_by``): ``finish`` of a
blocked run writes the row, ``finish`` of a succeeded run of the task deletes it, and so
does ``clear_priority`` (the operator in the kanban). The selection puts these tasks and
their blockers before the kanban's queue (``queue.priority_order``); a task no longer
``todo`` (done, cancelled) has no priority any more (``prune_priority``). ``queue_prefs``
returns the rows with the queue preferences.

``harness_quota`` holds the harnesses that ran out of quota on this computer and when they
may have it again (``retry_at``); ``project_harness`` the harness of a project's quota chain
(``quota_switch``) the project's runs are on now (``run/quota_chain.py``). Both are machine
state, like the queue preferences, and need no backlog commit.

``task_runs.retries`` is a JSON list, one item per retry of the run (``factory task retry``,
``run.retry``): when it started, the phase it continued from, the operator's note and the
base it was rebased onto. A retry is another attempt of the same run: ``reclaim`` turns the
failed, stopped or aborted row back into ``running`` under the same ``run_id`` (the same
task lock as ``claim``), so its phases, tokens and costs add up in the same trace session.

``task_runs.archived`` (0 or 1) hides a finished run from the dashboard's list of
active runs (``archive``, ``archive_finished``, ``unarchive``). ``delete_run`` and
``delete_archived`` erase archived runs: the ``task_runs`` row and the run's rows in
the engine's trace tables, in one transaction. A running run is never archived.

``claim`` inserts the ``running`` row in a ``BEGIN IMMEDIATE`` transaction, so a
second start of the same task fails with ``already_running`` even when two
processes race. A ``running`` row whose process is gone becomes ``aborted``.
``mark_stopped`` sets ``stopped`` (``factory task stop``, the dashboard); a
stopped run stays stopped: ``finish`` of the dying process does not overwrite it.
``task_runs.pause`` is the pause of a running run (``factory task pause|resume``):
NULL, ``pausing`` (asked for; the current phase still runs) or ``paused`` (the run's
process waits before its next phase). The state stays ``running`` meanwhile, so a paused
run keeps its task lock, its place among the parallel runs and its chain waiting.
``request_pause``, ``request_resume``, ``enter_pause`` and ``pause_of`` manage it;
``finish``, ``mark_stopped`` and the reaping of a dead run clear it.
``serialized`` is a lock file next to the trace DB (``trace.db.lock``, ``flock``)
held around the few steps that touch the shared ``.git`` of the main checkout;
``claim`` takes it too, so no run starts inside such a block. It is not the DB
write lock: no write transaction is open while git (or anything slow) runs, so
other runs and the dashboard can write meanwhile.

Every connection waits up to ``BUSY_TIMEOUT`` seconds for a busy DB (SQLite's
busy handler retries until then) instead of failing at once; when the wait runs
out the error is ``sqlite3.OperationalError`` "database is locked", which the
dashboard shows as ``trace_db_locked`` with a plain message (see ``is_db_busy``).

The store switches the trace DB to WAL before the engine's Tracer opens it: the
Tracer asks for WAL before it sets ``busy_timeout`` and would fail at once on a
fresh DB while another run holds the write lock.
"""

from __future__ import annotations

from aifactory.runstore.chains import ChainRepo
from aifactory.runstore.prs import PrRepo
from aifactory.runstore.quota import QuotaRepo
from aifactory.runstore.runs import RunRepo


class TaskRunStore(RunRepo, PrRepo, ChainRepo, QuotaRepo):
    """The trace store: every repository of the trace DB over one sqlite connection."""
