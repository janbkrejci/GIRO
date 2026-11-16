"""``giro config set|remove|move``: manage the repo's own copies of library items.

A repo inherits agents, workflows, skills and extensions from the library
(``aifactory.library.inherit``); what it has in ``.factory/`` (and ``.claude/skills/``) is
its own copy and wins. ``set agent NAME`` changes only the bindings of the agent in
``agents.yaml`` (harness, model, thinking, tools, writes, color); an agent the repo
inherits gets an entry that overrides just those keys. ``remove TYPE NAME`` deletes the
repo copy, refused with ``in_use`` while the library lacks the item and a workflow of the
repo or ``roles.yaml`` names the agent, a backlog task in base names the workflow, or a
roster agent binds the skill or extension. ``move TYPE NAME`` ("Move to library") writes
the repo copy into the library and deletes it from the repo: the library commit is made
first (lock, fetch, push without force, fast-forward), then the repo change.

``agents.yaml`` is edited by a ruamel.yaml round trip (``config.yaml`` and ``roles.yaml``
are only read), so comments and key order stay.

Every command computes a plan with a ``digest`` (``publish.plan_digest`` over the changed
paths, for ``move`` combined with the library plan). Targets: ``worktree`` (the default:
the files are written to the working tree, nothing is committed; ``giro config commit``
commits them later), ``direct`` (``--commit``: one commit on base through
``providers.publish``, pushed before base moves, the files reach the checkout afterwards)
and ``pr`` (``--commit --pr``). ``--dry-run`` returns the plan of the target; ``--expect
DIGEST`` refuses a plan that changed since (``plan_changed``). The worktree target reads
the working tree, the others the tree of base. The planned configuration must load and
every workflow of the repo must pass its preflight (``invalid_plan``).

Blockers (the first one is raised, exit 2): ``run_in_progress`` and ``invalid_plan``
(worktree); ``dirty_paths``, ``invalid_plan``, ``not_on_base``, ``run_in_progress``,
``base_behind`` and ``base_diverged`` (direct); ``dirty_paths`` and ``invalid_plan`` (pr).

Raised: ``invalid_value``, ``unknown_item``, ``invalid_item``, ``in_use``,
``library_missing`` (move without a library), ``unknown_base``, ``invalid_config``,
``plan_changed`` and the publish codes (``commit_failed``, ``base_moved``,
``push_failed``, ``fetch_failed``).
"""

from typing import TYPE_CHECKING

from aifactory.lazyexports import lazy_exports

if TYPE_CHECKING:
    from aifactory.library.config_edit.execute import (
        ConfigResult,
        execute_plan,
        run_config,
        write_worktree,
    )
    from aifactory.library.config_edit.plan import (
        ConfigPlan,
        finish_plan,
        plan_config,
        plan_move,
        plan_remove,
        plan_set,
    )
    from aifactory.library.config_edit.state import Change, Command, RepoState, read_state

__getattr__, __dir__ = lazy_exports(
    __name__,
    {
        "aifactory.library.config_edit.execute": (
            "ConfigResult",
            "execute_plan",
            "run_config",
            "write_worktree",
        ),
        "aifactory.library.config_edit.plan": (
            "ConfigPlan",
            "finish_plan",
            "plan_config",
            "plan_move",
            "plan_remove",
            "plan_set",
        ),
        "aifactory.library.config_edit.state": ("Change", "Command", "RepoState", "read_state"),
    },
)

__all__ = [
    "Change",
    "Command",
    "ConfigPlan",
    "ConfigResult",
    "RepoState",
    "execute_plan",
    "finish_plan",
    "plan_config",
    "plan_move",
    "plan_remove",
    "plan_set",
    "read_state",
    "run_config",
    "write_worktree",
]
