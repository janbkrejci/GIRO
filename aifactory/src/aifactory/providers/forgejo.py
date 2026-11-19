"""Pull requests and commit checks on a self-hosted Forgejo instance."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from functools import cached_property
from itertools import count
from time import sleep
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from aifactory.providers import git
from aifactory.providers.base import (
    CHECKS_FAILING,
    CHECKS_NONE,
    CHECKS_PASSING,
    CHECKS_PENDING,
    CLOSED,
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
from aifactory.providers.forgejo_rest import TOKEN_ENV, URL_ENV, ForgejoHttpError, ForgejoRest

_FAILED = {"error", "failure"}
_PASSED = {"success", "warning", "skipped"}
_SCP = re.compile(r"(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>[^\\].*)")
_JOB_PATH = re.compile(
    r"(?P<repo>.+)/actions/runs/(?P<run>[1-9][0-9]{0,18})/jobs/(?P<index>[0-9]{1,19})"
)
_LOG_LINES = 150
_MERGE_RETRY_DELAY = 3.0


def _object(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ProviderError("forgejo_failed", "Forgejo REST expected a JSON object")
    return data


def _items(data: Any) -> list[dict[str, Any]]:
    if data is None:  # Go may encode an empty list as null.
        return []
    if not isinstance(data, list):
        raise ProviderError("forgejo_failed", "Forgejo REST expected a JSON list")
    return [_object(item) for item in data]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class ForgejoProvider(GitProvider):
    name = "forgejo"
    reports_checks = True

    @cached_property
    def _api(self) -> ForgejoRest:
        return ForgejoRest(
            os.environ.get(URL_ENV, "").strip(), os.environ.get(TOKEN_ENV, "").strip()
        )

    @cached_property
    def _repo(self) -> str:
        """The owner/repo from the selected git remote, on the configured Forgejo host."""
        api = self._api
        remote = git.remote_url(self.root, self.settings.remote) or ""
        host, path, scheme = None, "", ""
        try:
            if "://" in remote:
                url = urlsplit(remote)
                scheme = url.scheme
                if url.scheme == "ssh" or (url.scheme == "https" and (url.port or 443) == api.port):
                    host, path = url.hostname, url.path
            elif match := _SCP.fullmatch(remote):
                host, path = match["host"].lower(), match["path"]
        except ValueError:
            pass
        path = unquote(path)
        prefix = unquote(api.prefix)
        if prefix and path.startswith(prefix + "/"):
            path = path.removeprefix(prefix + "/")
        elif prefix and scheme == "https":
            path = ""
        segments = path.strip("/").split("/")
        segments[-1] = segments[-1].removesuffix(".git")
        if (
            host != api.host
            or len(segments) != 2
            or any(part in ("", ".", "..") or "\\" in part for part in segments)
        ):
            raise ProviderError(
                "invalid_config",
                f"git remote {self.settings.remote!r} must point to an owner/repo on {URL_ENV}",
            )
        return "/".join(quote(part, safe="") for part in segments)

    @property
    def _repos(self) -> str:
        return f"/repos/{self._repo}"

    def _pr_path(self, pr: PullRequest) -> str:
        if not re.fullmatch(r"[1-9][0-9]*", pr.id):
            raise ProviderError("forgejo_failed", "Forgejo pull request number must be positive")
        if pr.url:
            expected = unquote(f"{self._api.prefix}/{self._repo}/pulls/{pr.id}")
            try:
                url = urlsplit(pr.url)
                matches = (
                    url.scheme == "https"
                    and url.hostname == self._api.host
                    and (url.port or 443) == self._api.port
                    and unquote(url.path).rstrip("/").casefold() == expected.casefold()
                )
            except ValueError:
                matches = False
            if not matches:
                raise ProviderError(
                    "forgejo_failed",
                    "Pull request URL does not match the configured Forgejo repository",
                )
        return f"{self._repos}/pulls/{pr.id}"

    def _pages(
        self, path: str, query: dict[str, str], *, field: str | None = None
    ) -> Iterator[dict[str, Any]]:
        seen = 0
        for page in count(1):
            data, total = self._api.json(
                "GET", path, query={**query, "page": str(page), "limit": "50"}
            )
            items = _items(_object(data).get(field) if field else data)
            yield from items
            seen += len(items)
            if not items or total is None or seen >= total:
                return

    def _pull_request(self, data: dict[str, Any], branch: str) -> PullRequest:
        number, title = data.get("number"), data.get("title")
        url = _text(data.get("html_url"))
        if type(number) is not int or number <= 0 or url is None or not isinstance(title, str):
            raise ProviderError("forgejo_failed", "Forgejo REST returned an invalid pull request")
        return PullRequest(str(number), url, branch, self.settings.base, title)

    def create_pr(self, branch: str, title: str, body: str) -> PullRequest:
        data, _ = self._api.json(
            "POST",
            f"{self._repos}/pulls",
            body={"head": branch, "base": self.settings.base, "title": title, "body": body},
        )
        return self._pull_request(_object(data), branch)

    def find_open_pr(self, branch: str) -> PullRequest | None:
        for data in self._pages(
            f"{self._repos}/pulls", {"state": OPEN, "head": branch, "base": self.settings.base}
        ):
            head_repo = _object(data.get("head")).get("repo_id")
            if (
                type(head_repo) is int
                and head_repo > 0
                and head_repo == _object(data.get("base")).get("repo_id")
            ):
                return self._pull_request(data, branch)
        return None

    def update_pr(self, pr: PullRequest, body: str) -> None:
        self._api.request("PATCH", self._pr_path(pr), body={"body": body})

    def comment(self, pr: PullRequest, body: str) -> None:
        # A comment-only review uses write:repository. The issue comment endpoint
        # would require the additional write:issue scope, even on a pull request.
        self._api.request(
            "POST", f"{self._pr_path(pr)}/reviews", body={"event": "COMMENT", "body": body}
        )

    def status(self, pr: PullRequest) -> PrStatus:
        obj, _ = self._api.json("GET", self._pr_path(pr))
        data = _object(obj)
        head = _text(_object(data.get("head")).get("sha"))
        if data.get("merged") is True:
            return PrStatus(
                MERGED,
                head_sha=head,
                merge_sha=_text(data.get("merge_commit_sha")),
                merged_at=utc_iso(data.get("merged_at")),
            )
        state = data.get("state")
        if state == OPEN:
            # False also covers drafts and a mergeability check in progress.
            return PrStatus(OPEN, MERGEABLE if data.get("mergeable") is True else UNKNOWN, head)
        if state == CLOSED:
            return PrStatus(CLOSED, head_sha=head)
        raise ProviderError("forgejo_failed", "Forgejo REST returned an invalid pull request state")

    def merge(
        self, pr: PullRequest, head_sha: str, subject: str, strategy: str | None = None
    ) -> str | None:
        chosen = self._strategy(strategy)
        if not head_sha.strip():
            raise MergeFailed("merge_failed", "Forgejo merge requires the expected head commit")
        try:
            before = self.status(pr)
        except ProviderError as exc:
            raise MergeFailed("merge_failed", exc.message) from exc
        if before.state == MERGED:
            return before.merge_sha
        if before.state == CLOSED or before.head_sha != head_sha:
            raise MergeFailed("merge_failed", f"PR {pr.id} is closed or its head has changed")

        retry_allowed = True
        while True:
            failure = None
            try:
                self._api.request(
                    "POST",
                    f"{self._pr_path(pr)}/merge",
                    body={"Do": chosen, "head_commit_id": head_sha, "MergeTitleField": subject},
                )
            except ProviderError as exc:
                failure = exc
            try:
                after = self.status(pr)
            except ProviderError as exc:
                if failure is None:
                    return None  # The POST succeeded. The merge SHA is unavailable.
                raise MergeFailed("merge_failed", failure.message) from exc
            if after.state == MERGED:
                return after.merge_sha
            if failure is None:
                raise MergeFailed(
                    "merge_failed", f"Forgejo did not confirm the merge of PR {pr.id}"
                )
            # Forgejo can return 405 while it computes mergeability. Retry only a
            # confirmed rejection, with the same expected head in the POST.
            if not (
                retry_allowed
                and isinstance(failure, ForgejoHttpError)
                and failure.status == 405
                and after.state == OPEN
                and after.head_sha == head_sha
            ):
                raise MergeFailed("merge_failed", failure.message) from failure
            retry_allowed = False
            sleep(_MERGE_RETRY_DELAY)

    def _commit_statuses(self, pr: PullRequest) -> Iterator[dict[str, Any]]:
        head = self.status(pr).head_sha
        if not head:
            raise ProviderError(
                "forgejo_failed", "Forgejo REST returned no pull request head commit"
            )
        return self._pages(
            f"{self._repos}/commits/{quote(head, safe='')}/status", {}, field="statuses"
        )

    def checks(self, pr: PullRequest) -> ChecksStatus:
        statuses = list(self._commit_statuses(pr))
        if not statuses:
            return ChecksStatus(CHECKS_NONE)
        failing = tuple(
            _text(item.get("context")) or "?"
            for item in statuses
            if _text(item.get("status")) in _FAILED
        )
        if failing:
            return ChecksStatus(CHECKS_FAILING, failing)
        if any(_text(item.get("status")) not in _PASSED for item in statuses):
            return ChecksStatus(CHECKS_PENDING)
        return ChecksStatus(CHECKS_PASSING)

    def _job_ref(self, target: object) -> tuple[str, int] | None:
        if not isinstance(target, str):
            return None
        try:
            url = urlsplit(target)
            if url.netloc and url.hostname != self._api.host:
                return None
        except ValueError:
            return None
        match = _JOB_PATH.fullmatch(unquote(url.path))
        if (
            match is None
            or match["repo"].casefold() != unquote(f"{self._api.prefix}/{self._repo}").casefold()
        ):
            return None
        return match["run"], int(match["index"])

    def _run_jobs(self, number: str) -> list[dict[str, Any]]:
        # The web link uses a repository run number. The jobs API uses a global run ID.
        data, _ = self._api.json("GET", f"{self._repos}/actions/runs", query={"run_number": number})
        runs = _items(_object(data).get("workflow_runs"))
        if not runs:
            return []
        run_id = runs[0].get("id")
        if type(run_id) is not int or run_id <= 0:
            raise ProviderError("forgejo_failed", "Forgejo REST returned an invalid Actions run ID")
        jobs, _ = self._api.json("GET", f"{self._repos}/actions/runs/{run_id}/jobs")
        return _items(jobs)

    def check_logs(self, pr: PullRequest, failing: tuple[str, ...]) -> dict[str, str]:
        if not failing:
            return {}
        links = {
            _text(item.get("context")) or "?": self._job_ref(item.get("target_url"))
            for item in self._commit_statuses(pr)
            if _text(item.get("status")) in _FAILED
        }
        runs: dict[str, list[dict[str, Any]] | ProviderError] = {}
        logs: dict[str, str] = {}
        for name in failing:
            ref = links.get(name)
            if ref is None:
                logs[name] = "log unavailable: no Forgejo Actions job link"
                continue
            number, index = ref
            if number not in runs:
                try:
                    runs[number] = self._run_jobs(number)
                except ProviderError as exc:
                    runs[number] = exc
            jobs = runs[number]
            if isinstance(jobs, ProviderError):
                logs[name] = f"log unavailable: {jobs.message}"
                continue
            # The API and the commit status link use the same ID-ordered job list.
            job_id = jobs[index].get("id") if index < len(jobs) else None
            if type(job_id) is not int or job_id <= 0:
                logs[name] = "log unavailable: Forgejo Actions job not found"
                continue
            try:
                raw, _ = self._api.request(
                    "GET", f"{self._repos}/actions/jobs/{job_id}/logs", log=True
                )
            except ProviderError as exc:
                logs[name] = f"log unavailable: {exc.message}"
            else:
                lines = raw.decode("utf-8", errors="replace").splitlines()[-_LOG_LINES:]
                logs[name] = "\n".join(lines) or "log empty"
        return logs
