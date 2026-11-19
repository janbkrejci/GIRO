"""Provider ``azure``: pull requests on Azure DevOps through the ``az`` CLI.

Configuration lives in ``.factory/config.yaml``::

    git_provider: azure
    azure:
      organization: contoso          # or https://dev.azure.com/contoso
      project: Fabrikam
      repository: fabrikam-app

PR status/list and CI reads use pooled HTTPS on Azure DevOps Services when the
current credentials are available. Writes use the same transport, with read-back
after uncertain outcomes; unsupported network/auth configurations retain the CLI.
No PR or CI results are cached. See ``azure_rest``.

Every ``az`` call goes through ``AzCli.run``. The executable is ``az`` found on
``PATH`` (or ``$GIRO_AZ``); tests put a fake one first on ``PATH``. Every call
passes ``--output json --only-show-errors``; ``az repos`` calls also pass
``--detect false`` and an explicit ``--org`` so nothing is guessed from git remotes.

Before the first operation the provider checks itself (``check``): ``az`` is
installed, the ``azure-devops`` extension is present and ``az`` is logged in
(``az account show``), unless ``$AZURE_DEVOPS_EXT_PAT`` holds a token. The result
is cached for at most 60 seconds in this process. The constructor never runs ``az``.

Commands used (shapes from the ``az repos pr`` / ``az devops invoke`` docs):

- ``az repos pr create`` -- ``--description`` takes one value per line. Real
  ``az`` prints ``repository.webUrl: null`` here and in ``pr show``/``pr list``,
  so the PR URL is built from the settings
  (``<organization_url>/<project>/_git/<repository>/pullrequest/<id>``);
- ``az repos pr show --id`` -- status; Azure computes ``mergeStatus``
  asynchronously (``queued``/``notSet``), so ``status`` asks again a bounded
  number of times, like the GitHub provider;
- ``az repos pr update --id --description`` -- a new PR description;
- ``az repos pr set-vote --id --vote approve`` -- approval after the final push;
  head checks before and after voting detect changes, but cannot be atomic;
- ``az repos pr update --id --status completed --squash true|false`` -- merge.
  The CLI has no ``--match-head-commit``: the head is compared with
  ``lastMergeSourceCommit`` right before completing, which leaves a small race
  window. Completion can be asynchronous, so the PR is polled afterwards;
- ``az devops invoke --area git --resource pullRequestThreads`` -- comment
  (``az repos pr`` has no comment command); the body goes in a temporary
  ``--in-file``;
- ``az repos pr policy list --id`` -- CI checks (``checks``): the evaluations of
  the PR's build validation (``Build``) and status (``Status``) policies. Other
  policies (reviewers, work items, comments) are not CI and are left out.
  ``approved`` is green, ``rejected``/``broken`` red, ``queued``/``running``
  pending, ``notApplicable`` is skipped. A PR without such a policy falls back to
  ``az pipelines runs list --branch`` (the latest run of each pipeline at the
  PR's head commit), so CI triggered by the branch push counts too;
- ``az devops invoke --area build --resource timeline`` and ``--resource logs``
  -- ``check_logs``: the issues and the log tail of each failed task of a red
  build (``az pipelines runs`` has no log command). A red status policy has no
  build, its entry is a note.

A missing PR is ``ERROR: TF401180 ...`` with exit 1 (``az_failed``). Unit tests
use a fake ``az`` (``tests/providers/az_fake.py``) that prints what real ``az``
prints; ``just validate --remote azure`` runs the PR lifecycle against a real
sandbox repo.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from aifactory.config.settings import AzureSettings, ProjectSettings
from aifactory.environment import canonical, value
from aifactory.providers import azure_rest
from aifactory.providers.base import (
    CHECKS_FAILING,
    CHECKS_NONE,
    CHECKS_PASSING,
    CHECKS_PENDING,
    CLOSED,
    CONFLICT,
    MERGEABLE,
    MERGED,
    OPEN,
    UNKNOWN,
    ChecksStatus,
    GitProvider,
    MergeFailed,
    ProviderError,
    PrStatus,
    PullRequest,
    utc_iso,
)

UNKNOWN_ATTEMPTS = 5
UNKNOWN_DELAY = 2.0
# How many of the newest PRs ``statuses`` lists; an older one is asked on its own.
LIST_LIMIT = 200
API_VERSION = "7.1"
PAT_ENV = "AZURE_DEVOPS_EXT_PAT"
AZ_TIMEOUT = 120
PREFLIGHT_TTL = 60.0
PREFLIGHT_MAX_ENTRIES = 128
_preflight_cache: dict[str, float] = {}
# Single-flight per context; unrelated repositories need not wait on a slow CLI.
_preflight_lock = threading.Lock()
_preflight_running: dict[tuple[object, str], Future[None]] = {}
WINDOWS = sys.platform == "win32"
# Azure DevOps refuses a PR description over 4000 characters; keep room for the note.
MAX_DESCRIPTION = 4000
TRUNCATED = "\n\n… (zkráceno na limit popisu PR v Azure DevOps, celé znění je v tasku a v běhu)"

CHECK_LOG_LINES = 150
"""How many trailing lines of a red check's log ``check_logs`` keeps."""
# Policy type ids of Azure DevOps (fixed across organizations).
BUILD_POLICY = "0609b952-1397-4640-95ec-e00a01b2c241"
STATUS_POLICY = "cbdc66da-9728-4af8-aada-9a5a32e4a226"
_CI_POLICIES = {BUILD_POLICY: "Build", STATUS_POLICY: "Status"}
_POLICY_STATES = {
    "approved": CHECKS_PASSING,
    "rejected": CHECKS_FAILING,
    "broken": CHECKS_FAILING,
    "queued": CHECKS_PENDING,
    "running": CHECKS_PENDING,
}
_RUNS_TOP = 50

_MISSING = "az (Azure CLI) is not installed or not on PATH; install it: https://aka.ms/azure-cli"
_NO_EXTENSION = (
    "the azure-devops extension of az is missing; run: az extension add --name azure-devops"
)
_NOT_LOGGED_IN = f"az is not logged in; run 'az login' or set {PAT_ENV}"
_NO_SECTION = (
    "git_provider 'azure' needs an 'azure' section with organization, project and repository"
)


class AzError(ProviderError):
    def __init__(self, args: tuple[str, ...], returncode: int, stderr: str) -> None:
        self.args_ = args
        self.returncode = returncode
        self.stderr = stderr
        lowered = stderr.lower()
        code = "az_not_logged_in" if "az login" in lowered or "tf400813" in lowered else "az_failed"
        detail = stderr.strip() or f"exit {returncode}"
        super().__init__(code, f"az {' '.join(args[:3])}: {detail}")


class AzCli:
    """The one place that runs ``az``."""

    def __init__(self, cwd: Path, executable: str | None = None) -> None:
        self.cwd = cwd
        self.executable = executable or value("AZ") or "az"

    def _argv(self, args: tuple[str, ...]) -> list[str]:
        if not WINDOWS:
            return [self.executable, *args]
        resolved = shutil.which(self.executable)
        if resolved is None:
            raise ProviderError("az_missing", _MISSING)
        shim = Path(resolved)
        if shim.suffix.lower() not in (".cmd", ".bat"):
            return [resolved, *args]
        # MSI layout only: never feed arbitrary arguments through cmd.exe.
        install = shim.parent.parent
        python = install / "python.exe"
        module = install / "Lib" / "site-packages" / "azure" / "cli" / "__main__.py"
        if (
            shim.stem.lower() == "az"
            and shim.parent.name.lower() == "wbin"
            and python.is_file()
            # MSI may ship only legacy bytecode, which Python can run with -m.
            and (module.is_file() or module.with_suffix(".pyc").is_file())
        ):
            return [str(python), "-I", "-X", "utf8", "-B", "-m", "azure.cli", *args]
        raise ProviderError(
            "az_missing",
            "Unsupported Azure CLI Windows shim; install the Azure CLI MSI or set "
            f"{canonical('AZ')} to a native executable (not a .cmd/.bat wrapper).",
        )

    def run(self, *args: str) -> str:
        try:
            result = subprocess.run(
                self._argv(args),
                cwd=self.cwd,
                input="",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=AZ_TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if WINDOWS else 0,
            )
        except FileNotFoundError as exc:
            raise ProviderError("az_missing", _MISSING) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("az_timeout", f"Azure CLI exceeded {AZ_TIMEOUT}s; retry") from exc
        except OSError as exc:
            raise ProviderError("az_missing", f"{_MISSING}; could not launch: {exc}") from exc
        if result.returncode != 0:
            raise AzError(args, result.returncode, result.stderr)
        return result.stdout

    def json(self, *args: str) -> Any:
        out = self.run(*args)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise ProviderError("az_failed", f"az {' '.join(args[:3])}: not JSON: {exc}") from exc


def _commit(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, dict) and isinstance(value.get("commitId"), str):
        return str(value["commitId"])
    return None


@dataclass(frozen=True)
class _Check:
    name: str
    state: str  # CHECKS_PASSING | CHECKS_PENDING | CHECKS_FAILING
    build_id: int | None = None
    note: str = ""  # where to look when there is no build log


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _policy_check(item: dict[str, Any]) -> _Check | None:
    """A CI check from one ``az repos pr policy list`` record; None for other policies."""
    config = _dict(item.get("configuration"))
    kind = _dict(config.get("type"))
    type_id = str(kind.get("id", "")).lower()
    kind_name = str(kind.get("displayName", ""))
    if type_id not in _CI_POLICIES and kind_name not in _CI_POLICIES.values():
        return None
    state = _POLICY_STATES.get(str(item.get("status", "")).lower())
    if state is None:  # notApplicable, or a status this code does not know
        return None
    settings = _dict(config.get("settings"))
    context = _dict(item.get("context"))
    build = context.get("buildId")
    build_id = build if isinstance(build, int) and not isinstance(build, bool) else None
    if kind_name == "Status" or type_id == STATUS_POLICY:
        genre = str(settings.get("statusGenre") or "")
        status_name = str(settings.get("statusName") or "")
        default = "/".join(part for part in (genre, status_name) if part) or "Status"
        name = str(settings.get("defaultDisplayName") or settings.get("displayName") or default)
        return _Check(name, state, None, f"status check {default}; see the PR in Azure DevOps")
    definition = settings.get("buildDefinitionId")
    default = f"Build {definition}" if definition is not None else "Build"
    name = str(settings.get("displayName") or context.get("buildDefinitionName") or default)
    return _Check(name, state, build_id)


def _run_check(item: dict[str, Any]) -> _Check:
    """A CI check from one ``az pipelines runs list`` record."""
    definition = _dict(item.get("definition"))
    name = str(definition.get("name") or f"pipeline {definition.get('id', '?')}")
    run = item.get("id")
    run_id = run if isinstance(run, int) and not isinstance(run, bool) else None
    if str(item.get("status", "")).lower() != "completed":
        return _Check(name, CHECKS_PENDING, run_id)
    result = str(item.get("result", "")).lower()
    passed = result in ("succeeded", "partiallysucceeded")
    return _Check(name, CHECKS_PASSING if passed else CHECKS_FAILING, run_id)


def _unique(checks: list[_Check]) -> list[_Check]:
    """``checks`` with repeated names numbered (``name (2)``), so names are keys."""
    seen: dict[str, int] = {}
    out: list[_Check] = []
    for check in checks:
        count = seen.get(check.name, 0) + 1
        seen[check.name] = count
        if count > 1:
            check = _Check(f"{check.name} ({count})", check.state, check.build_id, check.note)
        out.append(check)
    return out


def _fit(body: str) -> str:
    """``body`` cut at a line end so that it and the note fit Azure's limit (with margin)."""
    margin = 100  # the leading spaces _description_lines may add
    if len(body) <= MAX_DESCRIPTION - margin:
        return body
    limit = MAX_DESCRIPTION - margin - len(TRUNCATED)
    cut = body.rfind("\n", 0, limit)
    return body[: cut if cut > 0 else limit].rstrip() + TRUNCATED


def _description_lines(body: str) -> list[str]:
    """``body`` as ``--description`` values (one per line), within Azure's length limit.

    A line that looks like an option (``---``: starts with ``-``, no space) gets a
    leading space so argparse does not take it for one.
    """
    body = _fit(body)
    return [
        f" {line}" if line.startswith("-") and " " not in line else line
        for line in body.splitlines()
    ]


class AzureProvider(GitProvider):
    name = "azure"
    reports_checks = True

    def __init__(
        self,
        root: Path,
        settings: ProjectSettings,
        az: AzCli | None = None,
        *,
        attempts: int = UNKNOWN_ATTEMPTS,
        delay: float = UNKNOWN_DELAY,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError(f"attempts must be at least 1, got {attempts}")
        super().__init__(root, settings)
        if settings.azure is None:
            raise ProviderError("invalid_config", _NO_SECTION)
        self.azure: AzureSettings = settings.azure
        self.az = az or AzCli(root)
        self.attempts = attempts
        self.delay = delay
        self.sleep = sleep
        self._shared_preflight = az is None
        self._preflight_cache: dict[str, float] = {}

    # -- self-check ---------------------------------------------------------

    def check(self) -> None:
        """Fail with a clear ``ProviderError`` if ``az`` cannot work for this provider."""
        while True:
            if self._shared_preflight:
                self.az.executable = value("AZ") or "az"
            cache = _preflight_cache if self._shared_preflight else self._preflight_cache
            key = self._preflight_key()
            if key is None:
                self._check_uncached()
                return
            flight = (None if self._shared_preflight else self, key)
            with _preflight_lock:
                now = time.monotonic()
                for expired in [k for k, deadline in cache.items() if deadline <= now]:
                    del cache[expired]
                if key in cache:
                    return
                pending = _preflight_running.get(flight)
                if pending is None:
                    pending = Future()
                    _preflight_running[flight] = pending
                    break
            # Waiters share this wave's failure, but the completed flight is
            # removed so a later independent call can immediately retry.
            pending.result()
            if key == self._preflight_key():
                return
        try:
            self._check_uncached()
            # Login/extension commands can change files themselves. Never publish
            # success under a context that changed while validation was running.
            if key == self._preflight_key():
                with _preflight_lock:
                    while len(cache) >= PREFLIGHT_MAX_ENTRIES:
                        del cache[next(iter(cache))]
                    cache[key] = time.monotonic() + PREFLIGHT_TTL
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        else:
            pending.set_result(None)
        finally:
            with _preflight_lock:
                del _preflight_running[flight]

    def _preflight_key(self) -> str | None:
        """Hash context only; never retain plaintext environment or token contents.

        Content hashes ignore timestamp-only auth/config file touches. Installation
        metadata detects executable/extension changes. Logs and commandIndex are ignored;
        remote revocation is bounded by TTL and still surfaces in operation errors.
        Unreadable context disables caching, without masking CLI diagnostics.
        Injected clients have a private cache, even when the same client is reused.
        """
        try:
            config = Path(os.environ.get("AZURE_CONFIG_DIR") or Path.home() / ".azure")
            extensions = Path(os.environ.get("AZURE_EXTENSION_DIR") or config / "cliextensions")
            paths = [
                config / name
                for name in (
                    "config",
                    "clouds.config",
                    "azureProfile.json",
                    "accessTokens.json",
                    "msal_token_cache.json",
                    "msal_token_cache.bin",
                    "service_principal_entries.json",
                    "service_principal_entries.bin",
                    "azuredevops/config",
                    "azuredevops/personalAccessTokens",
                )
            ]
            contents = []
            for path in paths:
                digest: str | None
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except FileNotFoundError:
                    digest = None
                contents.append((str(path.resolve()), digest))
            paths = [extensions / "azure-devops"]
            executable = getattr(self.az, "executable", "")
            if self._shared_preflight:
                resolved = shutil.which(executable)
                if resolved is None:
                    return None
                paths.append(Path(resolved))
                argv = self.az._argv(())
                paths.append(Path(argv[0]))
                # Windows MSI dispatches through its bundled Python installation.
                if "azure.cli" in argv:
                    module = Path(argv[0]).parent / "Lib/site-packages/azure/cli/__main__"
                    paths += [module.with_suffix(".py"), module.with_suffix(".pyc")]
            stamps = []
            for path in paths:
                stamp: tuple[int, int, int, int, int] | None
                try:
                    stat = path.stat()
                    stamp = (
                        stat.st_dev,
                        stat.st_ino,
                        stat.st_size,
                        stat.st_mtime_ns,
                        stat.st_ctime_ns,
                    )
                except FileNotFoundError:
                    stamp = None
                stamps.append((str(path.resolve()), stamp))
            context = (
                sorted(
                    (name, val)
                    for name, val in os.environ.items()
                    if name.upper().startswith(("AZURE_", "ARM_"))
                    or name.upper()
                    in {
                        "GIRO_AZ",
                        "PATH",
                        "PATHEXT",
                        "HOME",
                        "USERPROFILE",
                        "APPDATA",
                        "LOCALAPPDATA",
                        "SYSTEMROOT",
                        "PYTHONPATH",
                        "PYTHONHOME",
                        "HTTP_PROXY",
                        "HTTPS_PROXY",
                        "NO_PROXY",
                        "REQUESTS_CA_BUNDLE",
                        "SSL_CERT_FILE",
                    }
                ),
                executable,
                str(self.root.resolve()),
                str(getattr(self.az, "cwd", self.root).resolve()),
                self.azure.organization_url,
                self.azure.project,
                self.azure.repository,
                contents,
                stamps,
            )
            return hashlib.sha256(json.dumps(context).encode()).hexdigest()
        except (OSError, ProviderError):
            return None

    def _check_uncached(self) -> None:
        try:
            self.az.run("--version")
        except AzError as exc:
            raise ProviderError("az_missing", f"{_MISSING} ({exc.message})") from exc
        try:
            self.az.run("extension", "show", "--name", "azure-devops", "--output", "json")
        except AzError as exc:
            raise ProviderError("az_devops_missing", _NO_EXTENSION) from exc
        if os.environ.get(PAT_ENV):
            return
        try:
            self.az.run("account", "show", "--output", "json")
        except AzError as exc:
            raise ProviderError("az_not_logged_in", _NOT_LOGGED_IN) from exc

    def _ready(self) -> None:
        self.check()

    # -- helpers ------------------------------------------------------------

    def _org(self) -> list[str]:
        return ["--org", self.azure.organization_url]

    @staticmethod
    def _tail() -> list[str]:
        return ["--detect", "false", "--output", "json", "--only-show-errors"]

    def _pr_url(self, data: dict[str, Any], pr_id: int) -> str:
        repository = data.get("repository")
        if isinstance(repository, dict) and isinstance(repository.get("webUrl"), str):
            return f"{str(repository['webUrl']).rstrip('/')}/pullrequest/{pr_id}"
        project = quote(self.azure.project, safe="")
        repo = quote(self.azure.repository, safe="")
        return f"{self.azure.organization_url}/{project}/_git/{repo}/pullrequest/{pr_id}"

    def _read(
        self,
        suffix: str,
        params: dict[str, str] | None = None,
        *,
        collection: bool = False,
        limit: int | None = None,
    ) -> Any:
        key = self._rest_key()
        if key is None:
            return azure_rest.UNAVAILABLE
        project = quote(self.azure.project, safe="")
        return azure_rest.read(
            key,
            self.azure.organization_url,
            self.az,
            suffix if suffix.startswith("/") else f"/{project}/_apis/{suffix}",
            params,
            collection=collection,
            limit=limit,
        )

    @staticmethod
    def _uncertain(exc: ProviderError, cli: bool) -> bool:
        if not cli:
            return isinstance(exc, azure_rest.WriteUncertain)
        if exc.code == "az_timeout":
            return True
        if exc.code != "az_failed":
            return False
        if isinstance(exc, AzError):
            # Explicit server rejections must not be hidden by an already matching state.
            detail = exc.stderr.lower()
            return any(
                word in detail
                for word in (
                    "timeout",
                    "timed out",
                    "connection",
                    "reset",
                    "aborted",
                    "eof",
                    "remote end closed",
                    "response ended",
                    "invalid json",
                )
            ) and not any(
                word in detail
                for word in (
                    "401",
                    "403",
                    "forbidden",
                    "unauthorized",
                    "permission",
                    "tf401",
                )
            )
        return True  # e.g. truncated/non-JSON CLI output after a successful exit

    def _rest_key(self) -> str | None:
        # Injected/custom CLI clients and unsupported network configurations keep
        # their existing transport. Never substitute a different authentication source.
        if (
            not self._shared_preflight
            or value("AZ")
            or not azure_rest.supported(self.azure.organization_url)
        ):
            return None
        config = Path(os.environ.get("AZURE_CONFIG_DIR") or Path.home() / ".azure")
        if not os.environ.get(PAT_ENV) and (config / "azuredevops/personalAccessTokens").exists():
            return None
        return self._preflight_key()

    def _write(self, suffix: str, method: str, body: dict[str, Any]) -> Any:
        key = self._rest_key()
        if key is None:
            return azure_rest.UNAVAILABLE
        project = quote(self.azure.project, safe="")
        return azure_rest.write(
            key,
            self.azure.organization_url,
            self.az,
            f"/{project}/_apis/{suffix}",
            method,
            body,
        )

    def _reviewer(self, *, cli: bool = False) -> str | None:
        data = self._read("/_apis/connectionData", {"api-version": "7.1-preview.1"})
        if data is azure_rest.UNAVAILABLE:
            if not cli:
                return None
            # connectionData is a bootstrap endpoint, absent from resourceAreas:
            # `az devops invoke --area location` cannot address it on Services.
            # Never substitute az account's identity for the CLI's stored PAT user.
            raise ProviderError(
                "az_failed", "Cannot establish CLI authenticated reviewer for read-back"
            )
        identity = _dict(_dict(data).get("authenticatedUser")).get("id")
        if not isinstance(identity, str) or not identity:
            raise ProviderError("az_failed", "Azure connectionData: missing authenticated user")
        return identity

    def _creation_candidates(self, branch: str) -> list[dict[str, Any]]:
        data = self._read(
            self._pr_path(),
            {
                "searchCriteria.status": "all",
                "searchCriteria.sourceRefName": self._ref(branch),
                "searchCriteria.targetRefName": self._ref(self.settings.base),
            },
            collection=True,
        )
        if data is azure_rest.UNAVAILABLE:
            data = self.az.json(
                "repos",
                "pr",
                "list",
                *self._org(),
                "--project",
                self.azure.project,
                "--repository",
                self.azure.repository,
                "--source-branch",
                branch,
                "--target-branch",
                self.settings.base,
                "--status",
                "all",
                *self._tail(),
            )
        if not isinstance(data, list) or any(
            not isinstance(item, dict) or type(item.get("pullRequestId")) is not int
            for item in data
        ):
            raise ProviderError("az_failed", "Azure PR list: invalid recovery snapshot")
        return data

    @staticmethod
    def _created_since(item: dict[str, Any], started: float) -> bool:
        try:
            created = datetime.fromisoformat(
                str(item.get("creationDate", "")).replace("Z", "+00:00")
            )
            return created.tzinfo is not None and created.timestamp() >= started
        except ValueError:
            return False

    @staticmethod
    def _ref(branch: str) -> str:
        return branch if branch.startswith("refs/") else f"refs/heads/{branch}"

    def _pr_path(self, pr_id: str | None = None) -> str:
        path = f"git/repositories/{quote(self.azure.repository, safe='')}/pullrequests"
        return path if pr_id is None else f"{path}/{quote(pr_id, safe='')}"

    # -- operations ---------------------------------------------------------

    def create_pr(self, branch: str, title: str, body: str) -> PullRequest:
        self._ready()
        args = [
            "repos",
            "pr",
            "create",
            *self._org(),
            "--project",
            self.azure.project,
            "--repository",
            self.azure.repository,
            "--source-branch",
            branch,
            "--target-branch",
            self.settings.base,
            "--title",
            title,
        ]
        lines = _description_lines(body)
        if lines:
            args += ["--description", *lines]
        started = time.time()
        # A failed snapshot does not block an ordinary create, but cannot support recovery.
        # A single new PR matching every requested field satisfies publication even
        # if a concurrent request created it. Never adopt an ID from the before set.
        # The timestamp also rejects older PRs omitted by a bounded CLI list; clock
        # skew can conservatively prevent recovery.
        try:
            before = {item["pullRequestId"] for item in self._creation_candidates(branch)}
        except ProviderError:
            before = None
        description = _fit(body)
        cli_write = False
        try:
            data = self._write(
                self._pr_path(),
                "POST",
                {
                    "sourceRefName": self._ref(branch),
                    "targetRefName": self._ref(self.settings.base),
                    "title": title,
                    "description": description,
                },
            )
            if data is azure_rest.UNAVAILABLE:
                cli_write = True
                description = "\n".join(lines)
                data = self.az.json(*args, *self._tail())
            if not isinstance(data, dict) or type(data.get("pullRequestId")) is not int:
                if not cli_write:
                    raise azure_rest.WriteUncertain("Azure PR create: no pullRequestId in output")
                raise ProviderError("az_failed", "az repos pr create: no pullRequestId in output")
        except ProviderError as exc:
            if not self._uncertain(exc, cli_write):
                raise
            matches: list[dict[str, Any]] = []
            if before is not None:
                try:
                    matches = [
                        item
                        for item in self._creation_candidates(branch)
                        if item["pullRequestId"] not in before
                        and self._created_since(item, started)
                        and item.get("sourceRefName") == self._ref(branch)
                        and item.get("targetRefName") == self._ref(self.settings.base)
                        and item.get("title") == title
                        and (item.get("description") or "") == description
                    ]
                except ProviderError:
                    pass
            if len(matches) != 1:
                raise
            data = matches[0]
        if not isinstance(data, dict) or not isinstance(data.get("pullRequestId"), int):
            raise ProviderError("az_failed", "az repos pr create: no pullRequestId in output")
        pr_id = int(data["pullRequestId"])
        return PullRequest(
            id=str(pr_id),
            url=self._pr_url(data, pr_id),
            branch=branch,
            base=self.settings.base,
            title=title,
        )

    def find_open_pr(self, branch: str) -> PullRequest | None:
        """Find the active PR for this source branch and configured target."""
        self._ready()
        data = self._read(
            self._pr_path(),
            {
                "searchCriteria.status": "active",
                "searchCriteria.sourceRefName": f"refs/heads/{branch.removeprefix('refs/heads/')}",
                "searchCriteria.targetRefName": f"refs/heads/{self.settings.base}",
                "$top": "1",
            },
            collection=True,
            limit=1,
        )
        if data is azure_rest.UNAVAILABLE:
            data = self.az.json(
                "repos",
                "pr",
                "list",
                *self._org(),
                "--project",
                self.azure.project,
                "--repository",
                self.azure.repository,
                "--source-branch",
                branch,
                "--target-branch",
                self.settings.base,
                "--status",
                "active",
                *self._tail(),
            )
        if not isinstance(data, list):
            raise ProviderError("az_failed", "az repos pr list: expected a JSON array")
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("pullRequestId"), int):
                raise ProviderError("az_failed", "az repos pr list: no pullRequestId in output")
            pr_id = int(item["pullRequestId"])
            return PullRequest(
                id=str(pr_id),
                url=self._pr_url(item, pr_id),
                branch=branch,
                base=self.settings.base,
                title=str(item.get("title", "")),
            )
        return None

    def _show(self, pr: PullRequest) -> dict[str, Any]:
        data = self._read(self._pr_path(pr.id))
        if data is azure_rest.UNAVAILABLE:
            data = self.az.json("repos", "pr", "show", "--id", pr.id, *self._org(), *self._tail())
        if not isinstance(data, dict):
            raise ProviderError("az_failed", "az repos pr show: expected a JSON object")
        return data

    @staticmethod
    def _parse(data: dict[str, Any]) -> PrStatus:
        head = _commit(data, "lastMergeSourceCommit")
        state = str(data.get("status", "")).lower()
        if state == "completed":
            return PrStatus(
                MERGED,
                head_sha=head,
                merge_sha=_commit(data, "lastMergeCommit"),
                merged_at=utc_iso(data.get("closedDate")),
            )
        if state == "abandoned":
            return PrStatus(CLOSED, head_sha=head)
        merge_status = str(data.get("mergeStatus", "")).lower()
        if merge_status == "succeeded":
            return PrStatus(OPEN, MERGEABLE, head_sha=head)
        if merge_status == "conflicts":
            return PrStatus(OPEN, CONFLICT, head_sha=head)
        return PrStatus(OPEN, UNKNOWN, head_sha=head)

    def _status_once(self, pr: PullRequest) -> PrStatus:
        return self._parse(self._show(pr))

    def status(self, pr: PullRequest) -> PrStatus:
        """The PR status; an open PR with ``unknown`` mergeability is asked again."""
        self._ready()
        for attempt in range(1, self.attempts + 1):
            status = self._status_once(pr)
            if status.state != OPEN or status.mergeability != UNKNOWN:
                return status
            if attempt < self.attempts:
                self.sleep(self.delay)
        return status

    def statuses(self, prs: Sequence[PullRequest]) -> dict[str, PrStatus | ProviderError]:
        """One ``az repos pr list --status all`` for the repository, paired by PR id.

        A PR outside the newest ``LIST_LIMIT``, or active with an unknown ``mergeStatus``,
        is asked with ``az repos pr show`` (``status``); so is every PR when the list fails.
        """
        if len(prs) < 2:
            return super().statuses(prs)
        listed: dict[str, PrStatus] = {}
        try:
            self._ready()
            data = self._read(
                self._pr_path(),
                {"searchCriteria.status": "all", "$top": str(LIST_LIMIT)},
                collection=True,
                limit=LIST_LIMIT,
            )
            if data is azure_rest.UNAVAILABLE:
                data = self.az.json(
                    "repos", "pr", "list", *self._org(),
                    "--project", self.azure.project, "--repository", self.azure.repository,
                    "--status", "all", "--top", str(LIST_LIMIT), *self._tail(),
                )  # fmt: skip
        except ProviderError:
            data = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and isinstance(item.get("pullRequestId"), int):
                listed[str(item["pullRequestId"])] = self._parse(item)
        return self._with_listed(prs, listed)

    def approve(self, pr: PullRequest, head_sha: str) -> bool:
        """Vote after the final push, guarded by head checks (neither vote is atomic).

        An ambiguous CLI vote fails closed when its authenticated reviewer cannot
        be established: account login may differ from the stored PAT identity.
        """
        self._ready()
        current = self._fresh_head(pr, head_sha)
        if current.state == MERGED:
            return False  # allow existing merge recovery without claiming a new review
        self._approval_head(pr, current, head_sha)
        reviewer = self._reviewer()
        cli_write = False
        try:
            result = azure_rest.UNAVAILABLE
            if reviewer is not None:
                result = self._write(
                    f"{self._pr_path(pr.id)}/reviewers/{quote(reviewer, safe='')}",
                    "PUT",
                    {"id": reviewer, "vote": 10},
                )
            if result is azure_rest.UNAVAILABLE:
                cli_write = True
                self.az.run(
                    "repos",
                    "pr",
                    "set-vote",
                    "--id",
                    pr.id,
                    "--vote",
                    "approve",
                    *self._org(),
                    *self._tail(),
                )
            elif _dict(result).get("id") != reviewer or _dict(result).get("vote") != 10:
                raise azure_rest.WriteUncertain("Azure approval returned an unexpected vote")
        except ProviderError as exc:
            if not self._uncertain(exc, cli_write):
                raise ProviderError("approve_failed", exc.message) from exc
            verified = False
            try:
                after = self._show(pr)
                self._approval_head(pr, self._parse(after), head_sha)
                reviewer = reviewer or self._reviewer(cli=True)
                verified = any(
                    _dict(item).get("id") == reviewer
                    and _dict(item).get("vote") == 10
                    and not _dict(item).get("isContainer")
                    for item in after.get("reviewers", [])
                )
            except ProviderError:
                pass
            if not verified:
                raise ProviderError(
                    "approve_failed", f"Azure approval vote for PR {pr.id} failed: {exc.message}"
                ) from exc
        self._approval_head(pr, self._status_once(pr), head_sha)
        return True

    def _fresh_head(self, pr: PullRequest, head_sha: str) -> PrStatus:
        """The PR status once Azure reports ``head_sha`` as its source commit.

        Azure updates ``lastMergeSourceCommit`` a moment after a push (the done
        commit pushed just before approval), so ask again a bounded number of times.
        """
        current = self._status_once(pr)
        for _ in range(self.attempts - 1):
            if current.state != OPEN or current.head_sha == head_sha:
                break
            self.sleep(self.delay)
            current = self._status_once(pr)
        return current

    @staticmethod
    def _approval_head(pr: PullRequest, current: PrStatus, head_sha: str) -> None:
        if current.state != OPEN or current.head_sha != head_sha:
            raise ProviderError(
                "approve_failed",
                f"PR {pr.id} approval head changed or PR not open: "
                f"{current.state}, {current.head_sha} != {head_sha}; merge stopped",
            )

    # -- CI checks ----------------------------------------------------------

    def _ci_checks(self, pr: PullRequest) -> list[_Check]:
        """The PR's CI checks: build and status policies, else the branch's pipeline runs."""
        self._ready()
        snapshot = self._read(self._pr_path(pr.id))
        data: Any = azure_rest.UNAVAILABLE
        if snapshot is not azure_rest.UNAVAILABLE:
            project = _dict(_dict(_dict(snapshot).get("repository")).get("project")).get("id")
            if not isinstance(project, str) or not project:
                raise ProviderError("az_failed", "Azure PR read: missing project id")
            data = self._read(
                "policy/evaluations",
                {
                    "artifactId": f"vstfs:///CodeReview/CodeReviewId/{project}/{pr.id}",
                    "api-version": "7.1-preview.1",
                },
                collection=True,
            )
        if data is azure_rest.UNAVAILABLE:
            data = self.az.json(
                "repos", "pr", "policy", "list", "--id", pr.id, *self._org(), *self._tail()
            )
        if not isinstance(data, list):
            raise ProviderError("az_failed", "az repos pr policy list: expected a JSON array")
        policies = [check for item in data if (check := _policy_check(_dict(item))) is not None]
        if policies:
            return _unique(policies)
        return _unique(self._run_checks(pr, snapshot))

    def _run_checks(self, pr: PullRequest, snapshot: Any = azure_rest.UNAVAILABLE) -> list[_Check]:
        """The latest pipeline run of each pipeline at the PR's head commit."""
        head = _commit(
            self._show(pr) if snapshot is azure_rest.UNAVAILABLE else _dict(snapshot),
            "lastMergeSourceCommit",
        )
        if head is None:
            return []
        data = self._read(
            "build/builds",
            {
                "branchName": f"refs/heads/{pr.branch}",
                "$top": str(_RUNS_TOP),
                "queryOrder": "queueTimeDescending",
            },
            collection=True,
            limit=_RUNS_TOP,
        )
        if data is azure_rest.UNAVAILABLE:
            data = self.az.json(
                "pipelines",
                "runs",
                "list",
                "--branch",
                f"refs/heads/{pr.branch}",
                "--project",
                self.azure.project,
                "--top",
                str(_RUNS_TOP),
                "--query-order",
                "QueueTimeDesc",
                *self._org(),
                *self._tail(),
            )
        if not isinstance(data, list):
            raise ProviderError("az_failed", "az pipelines runs list: expected a JSON array")
        latest: dict[object, _Check] = {}
        for item in data:
            run = _dict(item)
            if run.get("sourceVersion") != head:
                continue
            key = _dict(run.get("definition")).get("id", run.get("id"))
            if key not in latest:  # newest first
                latest[key] = _run_check(run)
        return list(latest.values())

    def checks(self, pr: PullRequest) -> ChecksStatus:
        """``failing`` when any check is red, else ``pending`` / ``passing`` / ``none``."""
        found = self._ci_checks(pr)
        if not found:
            return ChecksStatus(CHECKS_NONE)
        failing = tuple(check.name for check in found if check.state == CHECKS_FAILING)
        if failing:
            return ChecksStatus(CHECKS_FAILING, failing)
        if any(check.state == CHECKS_PENDING for check in found):
            return ChecksStatus(CHECKS_PENDING)
        return ChecksStatus(CHECKS_PASSING)

    def check_logs(self, pr: PullRequest, failing: tuple[str, ...]) -> dict[str, str]:
        """The issues and log tail of the failed tasks of each red check's build.

        Only the last ``CHECK_LOG_LINES`` lines are kept. A check without a build, or
        whose log cannot be read, gets ``log unavailable: ...`` instead; only a failure
        to read the checks themselves raises.
        """
        if not failing:
            return {}
        by_name = {check.name: check for check in self._ci_checks(pr)}
        logs: dict[str, str] = {}
        for name in failing:
            check = by_name.get(name)
            if check is None or check.build_id is None:
                note = check.note if check is not None and check.note else "no build"
                logs[name] = f"log unavailable: {note}"
                continue
            try:
                text = self._build_log(check.build_id)
            except ProviderError as exc:
                detail = (getattr(exc, "stderr", "") or exc.message).strip().splitlines()
                first = detail[0] if detail else exc.code
                logs[name] = f"log unavailable: {first} (build {check.build_id})"
                continue
            lines = text.splitlines()[-CHECK_LOG_LINES:]
            logs[name] = "\n".join(lines) if lines else f"log empty (build {check.build_id})"
        return logs

    def _invoke_build(self, resource: str, *route: str) -> Any:
        return self.az.json(
            "devops",
            "invoke",
            "--area",
            "build",
            "--resource",
            resource,
            "--route-parameters",
            f"project={self.azure.project}",
            *route,
            "--api-version",
            API_VERSION,
            *self._org(),
            "--output",
            "json",
            "--only-show-errors",
        )

    def _build_log(self, build_id: int) -> str:
        """The issues and log lines of the failed tasks of build `build_id`."""
        timeline = self._invoke_build("timeline", f"buildId={build_id}")
        records = _dict(timeline).get("records")
        if not isinstance(records, list):
            raise ProviderError("az_failed", "build timeline: no records")
        failed = [
            record
            for item in records
            if (record := _dict(item)).get("type") == "Task"
            and str(record.get("result", "")).lower() == "failed"
        ]
        failed.sort(key=lambda record: int(record.get("order") or 0))
        out: list[str] = []
        for record in failed:
            out.append(f"## {record.get('name', '?')}")
            for issue in record.get("issues") or []:
                issue = _dict(issue)
                if issue.get("message"):
                    out.append(f"{issue.get('type', 'error')}: {issue['message']}")
            log_id = _dict(record.get("log")).get("id")
            if log_id is None:
                continue
            lines = self._invoke_build("logs", f"buildId={build_id}", f"logId={log_id}")
            value = _dict(lines).get("value") if isinstance(lines, dict) else lines
            if isinstance(value, list):
                out += [str(line) for line in value[-CHECK_LOG_LINES:]]
        return "\n".join(out)

    def merge(
        self, pr: PullRequest, head_sha: str, subject: str, strategy: str | None = None
    ) -> str | None:
        """Complete `pr` if its head is still `head_sha`.

        REST completion sends the expected source commit atomically. The CLI has
        no match-head option, so its pre-check is paired with a completion head check;
        a concurrent push can only be detected after that CLI request.
        """
        chosen = self._strategy(strategy)
        current = self.status(pr)
        if current.state == MERGED:
            if current.head_sha != head_sha:
                raise MergeFailed("merge_failed", f"PR {pr.id} completed at a different head")
            return current.merge_sha
        if current.state == CLOSED:
            raise MergeFailed("merge_failed", f"PR {pr.id} is abandoned")
        if current.mergeability == CONFLICT:
            raise MergeFailed("conflict", f"PR {pr.id} has conflicts with {pr.base}")
        if current.head_sha != head_sha:
            raise MergeFailed("merge_failed", f"PR {pr.id} moved: {current.head_sha} != {head_sha}")
        cli_write = False
        try:
            resp = self._write(
                self._pr_path(pr.id),
                "PATCH",
                {
                    "status": "completed",
                    "lastMergeSourceCommit": {"commitId": head_sha},
                    "completionOptions": {
                        "mergeStrategy": "squash" if chosen == "squash" else "noFastForward",
                        "mergeCommitMessage": subject,
                        "deleteSourceBranch": False,
                        "bypassPolicy": False,
                    },
                },
            )
            if resp is azure_rest.UNAVAILABLE:
                cli_write = True
                resp = self.az.json(
                    "repos",
                    "pr",
                    "update",
                    "--id",
                    pr.id,
                    *self._org(),
                    "--status",
                    "completed",
                    "--squash",
                    "true" if chosen == "squash" else "false",
                    "--merge-commit-message",
                    subject,
                    "--delete-source-branch",
                    "false",
                    *self._tail(),
                )
        except ProviderError as exc:
            if exc.code == "az_not_logged_in":
                raise
            if not self._uncertain(exc, cli_write):
                # Read conflict state for the established diagnostic, never success.
                try:
                    conflict = self._status_once(pr).mergeability == CONFLICT
                except ProviderError:
                    conflict = False
                raise MergeFailed("conflict" if conflict else "merge_failed", exc.message) from exc
            try:
                return self._await_completion(pr, head_sha)
            except ProviderError as recovery:
                if recovery.code == "conflict":
                    raise recovery from exc
                raise MergeFailed("merge_failed", exc.message) from exc
        if isinstance(resp, dict) and str(resp.get("status", "")).lower() == "completed":
            if _commit(resp, "lastMergeSourceCommit") != head_sha:
                raise MergeFailed("merge_failed", f"PR {pr.id} completed at a different head")
            return _commit(resp, "lastMergeCommit")
        return self._await_completion(pr, head_sha)

    def _await_completion(self, pr: PullRequest, head_sha: str | None = None) -> str | None:
        for attempt in range(1, self.attempts + 1):
            status = self._status_once(pr)
            if head_sha is not None and status.head_sha != head_sha:
                raise MergeFailed("merge_failed", f"PR {pr.id} moved during completion")
            if status.state == MERGED:
                return status.merge_sha
            if status.state == CLOSED:
                raise MergeFailed("merge_failed", f"PR {pr.id} was abandoned while completing")
            if status.mergeability == CONFLICT:
                raise MergeFailed("conflict", f"PR {pr.id} has conflicts with {pr.base}")
            if attempt < self.attempts:
                self.sleep(self.delay)
        raise MergeFailed("merge_failed", f"PR {pr.id} not completed after {self.attempts} checks")

    def update_pr(self, pr: PullRequest, body: str) -> None:
        self._ready()
        args = ["repos", "pr", "update", "--id", pr.id, *self._org()]
        lines = _description_lines(body)
        if lines:
            args += ["--description", *lines]
        description = _fit(body)
        cli_write = False
        try:
            result = self._write(self._pr_path(pr.id), "PATCH", {"description": description})
            if result is azure_rest.UNAVAILABLE:
                cli_write = True
                description = "\n".join(lines)
                if not lines:
                    args += ["--description", ""]
                self.az.json(*args, *self._tail())
        except ProviderError as exc:
            if not self._uncertain(exc, cli_write):
                raise
            try:
                verified = self._show(pr).get("description") == description
            except ProviderError:
                verified = False
            if not verified:
                raise

    def comment(self, pr: PullRequest, body: str) -> None:
        self._ready()
        marker = uuid4().hex
        payload = {
            "properties": {"GIRO.OperationId": {"$type": "System.String", "$value": marker}},
            "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
            "status": 1,
        }
        cli_write = False
        try:
            result = self._write(f"{self._pr_path(pr.id)}/threads", "POST", payload)
            if result is azure_rest.UNAVAILABLE:
                cli_write = True
                self._comment_cli(pr, payload)
            elif type(_dict(result).get("id")) is not int:
                raise azure_rest.WriteUncertain("Azure comment returned no thread id")
        except ProviderError as exc:
            if not self._uncertain(exc, cli_write):
                raise
            verified = False
            try:
                threads = self._read(f"{self._pr_path(pr.id)}/threads", collection=True)
                if threads is azure_rest.UNAVAILABLE:
                    threads = self.az.json(
                        "devops",
                        "invoke",
                        "--area",
                        "git",
                        "--resource",
                        "pullRequestThreads",
                        "--route-parameters",
                        f"project={self.azure.project}",
                        f"repositoryId={self.azure.repository}",
                        f"pullRequestId={pr.id}",
                        "--http-method",
                        "GET",
                        "--api-version",
                        API_VERSION,
                        *self._org(),
                        "--output",
                        "json",
                        "--only-show-errors",
                    )
                    if isinstance(threads, dict):
                        threads = threads.get("value")
                for item in threads if isinstance(threads, list) else []:
                    thread = _dict(item)
                    prop = _dict(thread.get("properties")).get("GIRO.OperationId")
                    if isinstance(prop, dict):
                        prop = prop.get("$value")
                    if prop == marker and not thread.get("isDeleted"):
                        verified = any(
                            _dict(comment).get("content") == body
                            and not _dict(comment).get("isDeleted")
                            for comment in thread.get("comments", [])
                        )
                        if verified:
                            break
            except ProviderError:
                pass
            if not verified:
                raise

    def _comment_cli(self, pr: PullRequest, payload: dict[str, Any]) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(payload, handle)
            path = handle.name
        try:
            self.az.run(
                "devops",
                "invoke",
                "--area",
                "git",
                "--resource",
                "pullRequestThreads",
                "--route-parameters",
                f"project={self.azure.project}",
                f"repositoryId={self.azure.repository}",
                f"pullRequestId={pr.id}",
                "--http-method",
                "POST",
                "--in-file",
                path,
                "--api-version",
                API_VERSION,
                *self._org(),
                "--output",
                "json",
                "--only-show-errors",
            )
        finally:
            Path(path).unlink(missing_ok=True)
