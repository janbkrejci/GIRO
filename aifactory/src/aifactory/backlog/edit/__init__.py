"""Write operations on task files (add, edit, link), ``auto_continue``/``auto_merge`` flags and
the ``index.md`` of projects and steps (``add_container``, ``edit_container``).

Modules: ``common`` (errors, write lock, lookups, ids and the validated write shared by
all), ``task``, ``container`` (projects and steps) and ``batch`` (``add_batch``).

Every write is first applied to a copy of the backlog roots in a temporary directory
and validated with ``check_backlog``. A change that would add a new problem (a cycle,
an unknown reference, a duplicate id, ...) is rejected and the real tree stays
untouched. Problems that were in the backlog before do not block a write. Files are
replaced atomically and only files under a backlog root are ever written. Every
written file is recorded in the journal of ``aifactory/mainwrites.py``, so the guard of a
concurrent task run does not take the write for one of its agent.

Stable error codes (``TaskEditError.code`` / ``errors()[i]["code"]``):

=====================================  ====  ==========================================
code                                   exit  when
=====================================  ====  ==========================================
``invalid_config``                     2     invalid ``.factory/config.yaml`` (CLI)
``missing_backlog_dir``                2     a backlog root does not exist
``unknown_task``                       2     the id does not exist or is not a task
``unknown_step``                       2     no container with that id one level above tasks
``unknown_project``                    2     ``task list --project`` (CLI)
``unknown_container``                  2     no project or step with that id
``no_index``                           2     the container has no ``index.md``
``duplicate_id``                       2/1   target id is ambiguous (2); add would
                                             create a duplicate (1, from validation)
``invalid_id``, ``invalid_value``,     2     invalid input
``invalid_status``
``no_changes``, ``conflicting_options``, 2   wrong combination of options
``self_ref``
``not_done``                           2     ``follow_up_task`` of a task that is not done
``file_exists``                        2     the task file exists already
``outside_backlog``, ``write_failed``  2     path guard / I/O error
``backlog_invalid``                    1     the write would break the backlog; nothing
                                             was written. ``errors()`` lists the new
                                             problems with their ``backlog check``
                                             codes (``cycle``, ``unknown_ref``,
                                             ``id_prefix``, ``duplicate_id``, ...)
=====================================  ====  ==========================================
"""

from __future__ import annotations

from aifactory.backlog.edit.batch import (
    BatchResult as BatchResult,
)
from aifactory.backlog.edit.batch import (
    BatchTask as BatchTask,
)
from aifactory.backlog.edit.batch import (
    NewTask as NewTask,
)
from aifactory.backlog.edit.batch import (
    add_batch as add_batch,
)
from aifactory.backlog.edit.common import (
    EDITABLE_STATUSES as EDITABLE_STATUSES,
)
from aifactory.backlog.edit.common import (
    TaskEditError as TaskEditError,
)
from aifactory.backlog.edit.common import (
    compose_id as compose_id,
)
from aifactory.backlog.edit.common import (
    find_task as find_task,
)
from aifactory.backlog.edit.common import (
    load_for_edit as load_for_edit,
)
from aifactory.backlog.edit.common import (
    serialized_write as serialized_write,
)
from aifactory.backlog.edit.common import (
    slugify as slugify,
)
from aifactory.backlog.edit.common import (
    task_revision as task_revision,
)
from aifactory.backlog.edit.common import (
    write_lock as write_lock,
)
from aifactory.backlog.edit.container import (
    ContainerWriteResult as ContainerWriteResult,
)
from aifactory.backlog.edit.container import (
    add_container as add_container,
)
from aifactory.backlog.edit.container import (
    edit_container as edit_container,
)
from aifactory.backlog.edit.container import (
    find_container as find_container,
)
from aifactory.backlog.edit.container import (
    set_auto_continue as set_auto_continue,
)
from aifactory.backlog.edit.container import (
    set_auto_merge as set_auto_merge,
)
from aifactory.backlog.edit.task import (
    WriteResult as WriteResult,
)
from aifactory.backlog.edit.task import (
    add_task as add_task,
)
from aifactory.backlog.edit.task import (
    complete_task as complete_task,
)
from aifactory.backlog.edit.task import (
    edit_task as edit_task,
)
from aifactory.backlog.edit.task import (
    follow_up_commit_message as follow_up_commit_message,
)
from aifactory.backlog.edit.task import (
    follow_up_task as follow_up_task,
)
from aifactory.backlog.edit.task import (
    follow_up_title as follow_up_title,
)
from aifactory.backlog.edit.task import (
    human_done_entry as human_done_entry,
)
from aifactory.backlog.edit.task import (
    link_task as link_task,
)
