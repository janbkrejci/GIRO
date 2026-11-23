"""The dashboard API, one module per domain; ``web.app`` mounts them.

Each module has ``ROUTES`` (under ``/api``) and/or ``REPO_ROUTES`` (under
``/api/repos/{repo_id}``). The endpoints:

Endpoints: ``GET /api/health``; ``GET /api/runs?state=&task=&archived=`` (runs and cost
totals; ``archived=1`` lists the archived runs instead of the active ones),
``GET /api/runs/{run_id}`` (phases, gates, envelopes), ``GET /api/runs/{run_id}/events``
(trace events, ``after``/``limit`` cursor) and ``POST /api/runs/{run_id}/stop`` (the same
``stop_run`` as ``factory task stop``); ``POST /api/runs/{run_id}/pause`` and ``.../resume``
call ``pause_run`` and ``resume_run`` of ``factory task pause|resume`` (HTTP 409 when the run
is not running, already paused or not paused). ``POST /api/runs/{run_id}/publish`` is
``factory task publish`` for a succeeded run without a PR (its ``pr_error`` is in the run
summary). ``GET /api/runs/{run_id}/tail?events=&phases=&gates=
&envelopes=&open=&limit=`` returns only what is new since the rowid cursors (``cursors`` of
the detail or of the previous tail); ``open`` lists the phases the client shows as running.
``GET /api/runs/{run_id}/phases/{phase_id}/prompts`` returns the system and user prompt
of one phase from the run's session directory (``null`` for a missing file; ``source:
agent`` and ``legacy: true`` for an older run with only the agent's last prompts).
Archive: ``POST /api/runs/archive-finished`` archives every finished run,
``POST /api/runs/delete-archived`` erases every archived run with its trace,
``POST /api/runs/{run_id}/archive``, ``.../unarchive`` and ``.../delete`` act on one run
(archive refuses a running run, delete a run that is not archived; HTTP 409).
``GET /api/chains`` (``chains.py``) lists the auto-continue chains: their runs with results,
free slots (``max_parallel_runs``), the tasks skipped with their reasons and the tasks that
ran alone. ``POST /api/chains/{chain_id}/dismiss`` erases an ended chain (HTTP 409 while it
runs), its runs stay.

Live updates: ``GET /api/live`` is a Server-Sent Events stream (``live.py``). ``files``
events name changed paths under ``backlog/`` and ``.factory/``, ``trace`` events the runs
and tasks with new trace rows; ``resync`` asks the client to reload its screen. The
multi-repository dashboard serves each repository's stream at ``/api/repos/{id}/live`` and
all of them at ``/api/live`` (``live_all``, every event with its ``repo``), which drives the
overview; ``GET /api/repos/{id}/overview`` is one repository item of ``GET /api/overview``.

Backlog: ``GET /api/backlog?status=`` (tree, kanban tasks, workflows, steps),
``GET /api/backlog/names`` (``{levels, names: {id: {title, level}}}`` for every project,
step and task id, the tooltips of codes),
``GET /api/backlog/tasks/{task_id}`` (``factory task show`` plus both link directions),
``GET /api/backlog/tasks/{task_id}/documents`` (read-only: the task's backlog item, spec and
doc markdown, located by the task ID) and
``POST /api/backlog/tasks``, ``/api/backlog/tasks/{task_id}/edit`` and ``.../link``, which call
the same core functions as ``factory task add|edit|link``.
``POST /api/backlog/tasks/{task_id}/follow-up`` (``{body, title?, run?}``) creates a task
that follows a done task like ``factory task follow-up`` (with ``run`` it commits
the backlog and starts the run, HTTP 202); the task detail returns ``follows`` and
``followed_by``. A write that would break the
backlog is rejected with ``backlog_invalid`` (HTTP 422) and ``error.issues``. Writes stay in
the working tree: ``GET /api/backlog/status`` lists the backlog files not committed to base
(``{base, commit, clean, changes}``, shown on every screen) and ``POST /api/backlog/commit``
commits them all like ``factory backlog commit``. ``POST /api/backlog/sync`` runs
``factory backlog sync``: it opens (or extends) the sync PR that marks done the tasks whose PR
was merged outside factory; it is only ever started by this request, never automatically.

Run and auto-continue: ``GET /api/backlog/tasks/{task_id}/run-check`` (uncommitted config,
D4, unmet dependencies in base, a running run), ``POST /api/backlog/tasks/{task_id}/run``
(``{note?, force?}``, HTTP 202) starts ``factory task run`` as a separate process
(``launcher.py``); runs of several tasks run concurrently.
``GET /api/backlog/containers/{id}/graph`` returns the tasks of a project or step and
their ``depends_on`` edges, and ``POST /api/backlog/containers/{id}/auto-continue``
(``{mode: on|off|inherit}``) calls ``set_auto_continue`` like ``factory backlog auto-continue``;
``POST /api/backlog/containers/{id}/auto-merge`` likewise calls ``set_auto_merge``
(``factory backlog auto-merge``). The kanban's queue for auto-continue lives in the trace
DB (``task_queue``): ``POST /api/backlog/queue/order`` (``{order: [task ids]}``) saves the
order of ready tasks and ``POST /api/backlog/tasks/{task_id}/auto-exclude``
(``{excluded: bool}``) keeps a task out of auto-continue; ``GET /api/backlog`` returns
``queue_rank`` and ``auto_excluded`` of each task and lists the tasks in queue order.
``POST /api/backlog/tasks/{task_id}/archive`` (``{archived: bool}``) saves an archive
flag in the task front matter for done or cancelled tasks. Each task JSON includes
``archived``: absent flags default to true for cancelled tasks and false otherwise.
Tasks blocked during a run and their blockers go first (``task_priority``,
``queue.priority_order``): ``GET /api/backlog`` returns ``priority_rank``, ``priority_of``
and ``priority_blockers`` of each task and the records (``priority``, ``priority_order``);
``DELETE /api/backlog/tasks/{task_id}/priority`` takes the priority away.

Projects and steps: ``GET /api/backlog/containers/{id}`` (title, description, own values of
``index.md`` and effective values with their origin: a level or ``.factory/config.yaml``),
``POST /api/backlog/containers`` (``{parent?, id, title, body?}``) and
``POST /api/backlog/containers/{id}/edit`` (``{title?, workflow?, writes?, test?, source?,
target?, auto_continue?, clear?}``; ``null`` or ``clear`` removes a
key) call ``add_container`` and ``edit_container`` like ``factory backlog add|edit``.

Nový záměr (``web.intent``): ``POST /api/backlog/intent`` (``{intent}``, optionally with
``project`` or ``step``; without them the agent chooses the place) starts the proposer agent
like ``factory backlog propose``; ``GET /api/backlog/intent/{id}`` returns its state, the
progress of the current turn, the proposal and its resolved placement (``target``);
``POST .../refine`` (``{note, tasks?}``) continues the same agent session with a note and
the edited proposal; ``POST .../apply`` (``{tasks?}``) writes it and commits the backlog;
``POST .../discard`` closes it.

Lekce (``web.learn``): ``GET /api/learn`` lists the lessons of ``factory learn`` (rule,
occurrences with their runs, state, the task and its PR, and for an accepted lesson with a
merged PR the runs before and after the merge, the repos of its objections ``origin`` and
the ``repo`` of each objection) and the repo's scan job; ``POST /api/learn/scan``
(``{since?, all?}``, HTTP 202) starts ``factory learn scan`` in a thread (one per repo,
HTTP 409 ``busy``), with ``all: true`` over the objections of every registered repo
(``--all``: the job's result then also counts the repos and, by repo id, the lessons saved
in other repos, ``elsewhere``); ``POST /api/learn/lessons/{id}/accept`` (``{project?}``)
and ``.../dismiss`` (``{reason?}``) are ``factory learn accept`` and ``dismiss``.

Review: ``GET /api/review`` (open task PRs with mergeability and costs; ``?done=1``
adds the merged and closed task PRs in ``done``),
``GET /api/review/{task_id}`` (PR body, diff per file, gates and tests, the
reviewer's verdict, runs), ``POST /api/review/{task_id}/approve`` (HTTP 200),
``.../return`` (``{note}``, HTTP 202) and ``.../resolve`` (``{}``, HTTP 202). They call
``approve_task``, ``return_task`` and ``resolve_task`` of ``aifactory.review``, like
``factory task approve|return|resolve``; return and resolve start ``factory task
return|resolve`` as a separate process (``launcher.py``).
Approve sends an Azure approval vote, or a GitHub approve review as github_approver,
after the final push and before merge; otherwise it reports reviewed: false.

Settings: ``GET /api/settings`` (the form values of the shared ``.factory/config.yaml`` and
the machine-local ``.factory/local.yaml``, allowed options, problems of the files on disk and
the config status) and ``POST /api/settings`` (``{shared?: {...}, local?: {...}}``, only the
changed keys; ``null`` drops a key). ``local`` carries only ``trace_db``: the dashboard
port is in the registry (D22) and ``local.port`` is ``invalid_value``. Every value is
validated before anything is written; an invalid value is rejected with ``invalid_value``
(HTTP 422) and one ``error.issues`` entry per field (``id`` is the field name). Nothing is
committed and ``local.yaml`` is kept out of git. ``GET /api/config/status`` is
``factory config status``: shared configuration in ``.factory/`` with uncommitted changes
against ``base`` (D4), shown on every screen.

Factory tab (``factory.py``): ``GET /api/factory/check?offline=&fresh=`` is ``factory
check`` plus the registry finding ``trace_db_shared`` (another registered repository uses
the same trace DB); the result is kept 60 s per ``offline`` flag and ``fresh=1`` renews it
(``checked_at``, ``cached``); every answer also carries the package ``version`` and
only actionable findings (with a ``fix`` or an ``action``; no ``repo_installed``). It
answers HTTP 200 with the CLI envelope: ``ok``, or ``checks_failed`` with the report in
``data``. ``POST /api/factory/plan`` (``{action,
options?, target?}``) only reads and returns the plan with its ``digest`` (``init`` also
``detected``); ``action`` is ``init`` (options ``base``, ``provider``, ``azure``,
``docs_root``), ``config_commit``, ``set``, ``remove`` or ``move`` ("Move to library";
see factory.py for options);
``target`` is ``base`` (default) or ``pr``. ``POST /api/factory/apply``
(``{action, digest, options?, target?, message?}``) recomputes the plan like
``factory init|config commit --commit --expect``: another digest is HTTP
409 ``plan_changed`` with the new plan in ``data``, a blocker HTTP 409 with its code, a
rejected push HTTP 502 ``push_failed``; success is ``{commit, pushed, pr, warnings}``. The
digest does not depend on the target, so a rejected direct push can go as a PR with the
same digest. ``POST /api/config/pull`` is ``factory config pull``. Apply and pull run one
at a time per repository (HTTP 409 ``busy``); apply takes no paths or file contents, any
other body key is ``usage_error``. ``GET /api/factory/items`` lists the agents, workflows,
skills and extensions with their origin (``repo`` or ``library``), the library commit and
``library_name``. ``GET /api/factory/roster`` returns the effective agents with harness,
model and thinking, which of them ``agents.yaml`` overrides (``overridden``) and the
values without the overrides (``inherited``), ``presets``, ``thinking_levels`` and the
step overrides of the workflows
(``workflow_overrides``); ``POST /api/factory/roster`` (``{preset?, agent?, harness?,
model?, thinking?, reset?, dry_run}``; ``reset`` lists bindings of ``agent`` returned to
the inherited value) is ``factory config roster set`` on the working tree and
returns ``{agents, before, diff, changed, dry_run, comments_preserved}``, an invalid change
is HTTP 422 ``invalid_config``. ``move`` takes the global library write lock.

Limits: ``GET /api/limits`` (``limits.py``) lists the 5-hour and weekly session limits of
the Claude and Codex subscriptions the repository's harnesses use or this machine has, for
the topbar, as the harnesses last reported them in their runs and sessions. The multi-repo
app also serves the global ``GET /api/limits`` (no repository: only the subscriptions
available on this machine).

Global library: ``GET /api/machine/check?offline=&fresh=`` caches the outside-repo
CLI check; the server runs it once at start and again only for ``fresh=1``. It returns
only actionable findings (no info ``harness_missing`` of a harness no registered repo
uses; ``missing_harnesses`` names them all). A cached read evaluates the library rules
again (local git only). Concurrent requests share one running check with one time
budget; past it the answer is ``check_timeout`` with the last known result (``stale``,
``checked_at``) and ``progress`` (the unfinished step). ``GET /api/machine/progress``
returns the running check's ``step``, ``detail`` and ``elapsed`` (``job``) for the UI.
``GET /api/library`` returns status without fetch and item metadata (``?items=0``: status
only); ``GET /api/library/items/{type}/{name}?version=`` returns files and history.
``GET /api/library/overrides`` lists the registered repositories' own copies of library
items (``same``: the copy equals the library's version); "Diff" and "Move to library" use
the move plan of ``/api/repos/{id}/factory/plan``.
``POST /api/library/plan`` and ``/apply`` take ``{action, options?, digest?}`` for init,
clone, import and seed. Apply recomputes the reviewed digest. All writes including
``POST /api/library/pull`` and ``/push`` and ``POST /api/library/remote`` (``{url}``: share a
library without a remote through an empty git remote) share one nonblocking lock.
``?fetch=1`` on ``GET /api/library`` fetches the remote first. See ``library.py``
for validation and core adapters.

More repositories: ``create_multi_app`` serves every repository in ``dashboard.yaml`` of
the GIRO home (``aifactory.registry``). ``GET /api/health`` (``app: giro-dashboard``, ``version``,
``home``), ``GET /api/code`` and ``POST /api/restart`` (as above, no repository needed;
not served per repository), ``GET /api/limits`` (the machine's subscriptions),
``GET /api/repos`` (the repositories with ``status``, the ``factory`` state and ``demo``, true
for the sample in its default folder),
``POST /api/repos`` (``{path}``, idempotent; HTTP 201 when added; registers the repository
and, when its base has no ``.factory/config.yaml`` yet, commits it and the ``.gitignore``
lines it lacks; a repository with factory is only registered, without a commit),
``POST /api/repos/inspect`` (``{path}``, reads only), ``GET /api/repos/{id}/removal`` (what
removing commits), ``DELETE /api/repos/{id}`` (drops the registry entry and leaves the
repository as it is; ``{uninstall: true}`` also commits the removal of ``.factory/``),
``POST /api/demo`` (creates and registers the sample repository of ``factory try`` in its
default folder; HTTP 201 when created, 200 when it was there), ``DELETE /api/demo`` (drops the
sample's registry entry and deletes its default folder; ``run_in_progress`` while it runs) and
``GET``/``POST /api/dashboard/settings``
(``port``, ``home``, ``restart_required``). Choosing the folder to add (``fs.py``):
``GET /api/fs/dirs?path=`` lists the visible subdirectories of a directory under the user's
home (default: home) as
``{path, parent, entries: [{name, path, is_git, has_factory}], truncated}``, HTTP 403
``outside_home`` outside it; ``GET /api/fs/pick`` tells ``{available}`` and
``POST /api/fs/pick`` opens the system folder dialog on the server's session and answers
``{path}`` or ``{cancelled: true}`` (``picker_unavailable``, ``picker_busy``,
``picker_timeout``). ``GET /api/overview`` (``overview.py``) tells for every repository what
runs, waits for review and failed, how many tasks are in each board state, its parallel
slots (``factory``) and the state of its configuration; it only reads. With background
checks on, ``pr_refresh.py`` refreshes the open task PRs of every registered repository from
the provider every ``PR_REFRESH_INTERVAL`` seconds, so a PR merged outside factory leaves the
review lists without ``factory backlog sync``. The Overview's
factory controls (``control.py``) are ``POST /api/repos/{id}/control/start`` (start ready
tasks into the free ``max_parallel_runs`` slots, HTTP 200 also when nothing started),
``.../control/pause`` and ``.../control/resume`` (every running run) and
``.../control/stop`` (every running run, in the background; HTTP 202, the progress is in
the overview's ``factory.control``).
The hidden terminal (``terminal.py``, multi-repository app only):
``GET /api/terminal`` returns ``{enabled, cwd, shell, pty}`` in the JSON envelope.
There is no login: both allowed server hosts and the request client must be loopback
(``server.LOOPBACK``), otherwise GET reports disabled. The normal Origin, Sec-Fetch-Site
and JSON write guard applies.
``POST /api/terminal/sessions`` (``{cols, rows, cwd?}``) creates a POSIX login/interactive
PTY shell and returns ``{id}``; Windows returns 501 ``terminal_pty_unsupported``.
``GET /api/terminal/sessions/{id}/stream`` streams raw bytes to one client (409 if attached).
``POST .../{id}/input`` (``{data}``) writes UTF-8 and ``POST .../{id}/resize``
(``{cols, rows}``) resizes the PTY, notifying its foreground group via SIGWINCH.
``DELETE /api/terminal/sessions/{id}``, disconnect and shell exit kill the process group
and remove the session. All session routes require loopback server and client (403).
At most four sessions run (429); dimensions are 1–1000. TERM is xterm-256color,
COLORTERM truecolor, default cwd is home. Sessions have no time limit or persistence.

The hidden screen (``screen.py``, multi-repository app only): ``GET /api/screen``
returns ``{enabled, supported, reason, width, height}`` in the JSON envelope.
``GET /api/screen/stream?fps=15&max_width=1280&quality=60`` requires loopback
server and client (403), support (501 ``screen_unsupported``), and one subscriber
(409 if busy). Frames are a four-byte big-endian length followed by JPEG bytes;
unchanged raw frames are omitted. Limits are fps 1–30, width 320–3840, quality
20–95 (400 ``usage_error``). Disconnect and app shutdown release capture resources and held input.
``POST /api/screen/input`` accepts ``{events: [...]}`` with at most 200 mouse/key
operations and returns ``{applied, ignored}`` (unknown key codes). Coordinates
are 0-1 on the primary monitor; wheel deltas are detents, positive right/up.
Input requires loopback and the JSON write guard; invalid batches return 400
``usage_error`` before any input is sent, unsupported platforms return 501.
Input is transient and never logged or persisted.

The guide (``assistant.py``, multi-repository app only) reads, never writes:
``GET /api/assistant/threads`` (newest first), ``POST /api/assistant/threads``
(``{title?, harness?, screen?: {repo?, task?, run?}}``, HTTP 201; explicit harness takes
precedence over repository and computer defaults and uses its own computer-local model
and thinking. Without it, repository/computer inheritance and fallback are preserved.
Invalid context or a non-string/empty harness returns HTTP 400 ``usage_error``.
The guide supports claude and an isolated Codex CLI 0.167.2 transport. Explicit unknown,
disabled or unsupported choices (including pi) return HTTP 409 ``harness_unavailable``
before creating a thread, without fallback. The choice is stored for all later messages
and session resumes; an existing conversation cannot change harness.
Codex isolation failures are reported in the answer),
``GET /api/assistant/harnesses`` returns ``{harnesses: [{harness, model, thinking,
usable, reason}]}`` for supported, computer-enabled choices. An unusable local selection
has ``usable: false`` and an explanatory ``reason``; valid ones have ``reason: null``.
With no computer settings, empty model means the adapter default. Malformed computer
settings return HTTP 409 ``harness_unavailable``. This list never runs a model and
does not guarantee login or a successful run,
``GET /api/assistant/threads/{id}`` (with messages),
``DELETE /api/assistant/threads/{id}`` (the thread and its folder for good; HTTP 409 ``busy``
while an answer runs),
``POST /api/assistant/threads/{id}/messages`` (``{text, screen?: {repo, task, run}}``,
HTTP 202, HTTP 409 ``busy`` while an answer runs) and
``GET /api/assistant/threads/{id}/stream?after=SEQ`` (Server-Sent Events ``start``,
``text``, ``tool``, ``done`` or ``error``, then ``idle``). An answer may carry proposed
``actions``; the panel executes a confirmed one through the endpoints below (with their
guards) and reports it with ``POST /api/assistant/threads/{id}/actions/{action_id}``
(``{status: done|failed|dismissed, message?}``; HTTP 409 ``action_decided`` the second time),
which only records the outcome in the thread.
Each repository's endpoints above are under
``/api/repos/{id}/`` with the same relative paths (``/health`` excepted) and their own
``/live`` hub; an unknown id gives HTTP 404 ``unknown_repo``, a missing folder HTTP 404
``repo_missing``. Handlers read the repository from the request (``RepoScope``).

Write guard (``guard.py``): every POST, PUT, PATCH and DELETE under ``/api/`` is checked
before the handler. ``Origin``, if sent, must be ``http://<Host>`` and ``Sec-Fetch-Site``, if
sent, ``same-origin``, otherwise HTTP 403 ``cross_origin``; a body that is not
``application/json`` gets HTTP 415 ``unsupported_media_type``. Requests without these headers
(CLI, curl) and a POST without a body pass; GET is not checked.
"""
