"""Forgejo 17 wire contracts, with a fake HTTPS connection."""

from __future__ import annotations

import http.client
import json
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

import pytest

from aifactory.config.settings import ProjectSettings
from aifactory.providers import forgejo, forgejo_rest, get_provider
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
)
from gitutil import git

HOST = "forgejo.example"
PREFIX = "/forge"
BASE_URL = f"https://{HOST}{PREFIX}"
REPO = f"{PREFIX}/api/v1/repos/example/Omnibus-a2ui"
BRANCH = "factory/test-1"
PR = PullRequest("7", f"{BASE_URL}/example/Omnibus-a2ui/pulls/7", BRANCH, "main", "Change")
SHA = "a" * 40
MERGE_SHA = "b" * 40


def pull(**changes: Any) -> dict[str, Any]:
    return {
        "number": 7,
        "id": 222,
        "html_url": PR.url,
        "state": OPEN,
        "title": PR.title,
        "head": {"ref": BRANCH, "sha": SHA, "repo_id": 13},
        "base": {"ref": PR.base, "repo_id": 13},
        "mergeable": True,
        "merged": False,
        **changes,
    }


@dataclass
class Reply:
    status: int
    data: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self, size: int) -> bytes:
        return self.data[:size]

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name)


@dataclass
class Call:
    method: str
    target: str
    body: Any
    headers: dict[str, str]


class Wire:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[Call] = []
        self.replies: deque[Reply | Exception] = deque()
        monkeypatch.setattr(http.client, "HTTPSConnection", self.connect)

    def connect(self, host: str, port: int, *, timeout: float) -> Self:
        assert (host, port, timeout) == (HOST, 443, 30.0)
        return self

    def request(
        self, method: str, target: str, *, body: bytes | None, headers: dict[str, str]
    ) -> None:
        assert headers["Authorization"] == "token fixture-token"
        self.calls.append(Call(method, target, json.loads(body) if body else None, headers))

    def getresponse(self) -> Reply:
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self) -> None:
        pass

    def reply(self, data: Any, status: int = 200, /, **headers: str) -> None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.replies.append(Reply(status, body, headers))


@pytest.fixture
def provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitProvider:
    monkeypatch.setenv("GIRO_FORGEJO_URL", BASE_URL)
    monkeypatch.setenv("GIRO_FORGEJO_TOKEN", "fixture-token")
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "forge", f"git@{HOST}:example/Omnibus-a2ui.git")
    return get_provider(ProjectSettings(git_provider="forgejo", remote="forge"), tmp_path)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return Wire(monkeypatch)


def test_pull_request_lifecycle(provider: GitProvider, wire: Wire) -> None:
    wire.reply(pull(), 201)
    wire.reply(pull(), 201)
    wire.reply({}, 200)
    wire.reply(pull())
    wire.reply(pull(state=CLOSED, mergeable=False))
    wire.reply(
        pull(
            state=CLOSED,
            merged=True,
            merge_commit_sha=MERGE_SHA,
            merged_at="2026-10-16T10:30:01+02:00",
        )
    )

    assert provider.create_pr(BRANCH, PR.title, "initial") == PR
    provider.update_pr(PR, "edited")
    provider.comment(PR, "review summary")
    assert provider.status(PR) == PrStatus(OPEN, MERGEABLE, SHA)
    assert provider.status(PR) == PrStatus(CLOSED, head_sha=SHA)
    assert provider.status(PR) == PrStatus(
        MERGED, head_sha=SHA, merge_sha=MERGE_SHA, merged_at="2026-10-16T08:30:01.000+00:00"
    )
    assert [(c.method, c.target, c.body) for c in wire.calls[:3]] == [
        (
            "POST",
            f"{REPO}/pulls",
            {
                "head": BRANCH,
                "base": "main",
                "title": PR.title,
                "body": "initial",
            },
        ),
        ("PATCH", f"{REPO}/pulls/7", {"body": "edited"}),
        ("POST", f"{REPO}/pulls/7/reviews", {"event": "COMMENT", "body": "review summary"}),
    ]
    assert [c.target for c in wire.calls[3:]] == [f"{REPO}/pulls/7"] * 3


def test_find_open_pr_skips_same_branch_from_fork(provider: GitProvider, wire: Wire) -> None:
    wire.reply([pull(head={"ref": BRANCH, "repo_id": 81})], **{"X-Total-Count": "2"})
    wire.reply([pull()], **{"X-Total-Count": "2"})
    wire.reply([])
    assert provider.find_open_pr(BRANCH) == PR
    assert provider.find_open_pr("missing") is None
    url = urlsplit(wire.calls[1].target)
    assert url.path == f"{REPO}/pulls"
    assert parse_qs(url.query) == {
        "state": ["open"],
        "head": [BRANCH],
        "base": ["main"],
        "page": ["2"],
        "limit": ["50"],
    }


@pytest.mark.parametrize("strategy", [None, "merge"])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_merge_compares_head_and_reads_back(
    provider: GitProvider, wire: Wire, strategy: str | None, ambiguous: bool
) -> None:
    wire.reply(pull(mergeable=False))
    if ambiguous:
        wire.replies.append(TimeoutError("lost the response"))
    else:
        wire.reply(b"", 200)
    wire.reply(pull(merged=True, merge_commit_sha=MERGE_SHA))
    assert provider.merge(PR, SHA, "Title", strategy) == MERGE_SHA
    assert [c.method for c in wire.calls] == ["GET", "POST", "GET"]
    assert wire.calls[1].target == f"{REPO}/pulls/7/merge"
    assert wire.calls[1].body == {
        "Do": strategy or "squash",
        "head_commit_id": SHA,
        "MergeTitleField": "Title",
    }


def test_merge_rejects_missing_or_changed_head(provider: GitProvider, wire: Wire) -> None:
    with pytest.raises(MergeFailed, match="requires the expected head"):
        provider.merge(PR, "", "Title")
    assert wire.calls == []
    wire.reply(pull())
    with pytest.raises(MergeFailed, match="head has changed"):
        provider.merge(PR, MERGE_SHA, "Title")
    assert [c.method for c in wire.calls] == ["GET"]


def test_merge_rejected_after_head_moves_during_request(provider: GitProvider, wire: Wire) -> None:
    wire.reply(pull())
    wire.reply({"message": "head out of date"}, 409)
    wire.reply(pull(head={"sha": MERGE_SHA}))
    with pytest.raises(MergeFailed, match="HTTP 409"):
        provider.merge(PR, SHA, "Title")
    assert [c.method for c in wire.calls] == ["GET", "POST", "GET"]


@pytest.mark.parametrize("merged", [True, False])
def test_merge_retries_405_once(
    provider: GitProvider, wire: Wire, monkeypatch: pytest.MonkeyPatch, merged: bool
) -> None:
    delays: list[float] = []
    monkeypatch.setattr(forgejo, "sleep", delays.append)
    wire.reply(pull())
    wire.reply({}, 405)
    wire.reply(pull(mergeable=False))
    if merged:
        wire.reply(b"")
        wire.reply(pull(merged=True, merge_commit_sha=MERGE_SHA))
        assert provider.merge(PR, SHA, "Title") == MERGE_SHA
    else:
        wire.reply({}, 405)
        wire.reply(pull())
        with pytest.raises(MergeFailed, match="HTTP 405"):
            provider.merge(PR, SHA, "Title")
    assert delays == [3.0]
    assert [c.method for c in wire.calls] == ["GET", "POST", "GET", "POST", "GET"]
    assert wire.calls[3].body == wire.calls[1].body


def test_merge_does_not_retry_405_if_head_changed(
    provider: GitProvider, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[float] = []
    monkeypatch.setattr(forgejo, "sleep", delays.append)
    wire.reply(pull())
    wire.reply({}, 405)
    wire.reply(pull(head={"sha": MERGE_SHA}))
    with pytest.raises(MergeFailed, match="HTTP 405"):
        provider.merge(PR, SHA, "Title")
    assert delays == []
    assert [c.method for c in wire.calls] == ["GET", "POST", "GET"]


def test_false_mergeability_is_indeterminate(provider: GitProvider, wire: Wire) -> None:
    wire.reply(pull(mergeable=False))
    assert provider.status(PR) == PrStatus(OPEN, UNKNOWN, SHA)


@pytest.mark.parametrize(
    ("statuses", "result"),
    [
        (None, ChecksStatus(CHECKS_NONE)),
        (["success", "warning", "skipped"], ChecksStatus(CHECKS_PASSING)),
        (["success", "pending"], ChecksStatus(CHECKS_PENDING)),
        (["failure", "pending", "error"], ChecksStatus(CHECKS_FAILING, ("check-0", "check-2"))),
    ],
)
def test_checks_use_pr_head(
    provider: GitProvider, wire: Wire, statuses: list[str] | None, result: ChecksStatus
) -> None:
    wire.reply(pull())
    wire.reply(
        {
            "statuses": [
                {"status": state, "context": f"check-{index}"}
                for index, state in enumerate(statuses)
            ]
            if statuses
            else None
        }
    )
    assert provider.checks(PR) == result
    assert urlsplit(wire.calls[1].target).path == f"{REPO}/commits/{SHA}/status"


def test_checks_find_failures_on_later_status_pages(provider: GitProvider, wire: Wire) -> None:
    wire.reply(pull())
    wire.reply({"statuses": [{"status": "success"}], "total_count": 1}, **{"X-Total-Count": "2"})
    wire.reply(
        {"statuses": [{"status": "failure", "context": "test"}], "total_count": 1},
        **{"X-Total-Count": "2"},
    )
    assert provider.checks(PR) == ChecksStatus(CHECKS_FAILING, ("test",))
    assert parse_qs(urlsplit(wire.calls[-1].target).query)["page"] == ["2"]


def test_logs_resolve_web_numbers_to_api_ids(provider: GitProvider, wire: Wire) -> None:
    web = f"{PREFIX}/example/Omnibus-a2ui/actions/runs/15/jobs"
    wire.reply(pull())
    wire.reply(
        {
            "statuses": [
                {"context": "lint", "status": "failure", "target_url": f"{web}/0"},
                {"context": "test", "status": "failure", "target_url": f"https://{HOST}{web}/1"},
                {
                    "context": "external",
                    "status": "error",
                    "target_url": f"https://other.example{web}/2",
                },
            ]
        }
    )
    wire.reply({"workflow_runs": [{"id": 314, "index_in_repo": 15}]})
    wire.reply([{"id": 120}, {"id": 121}])
    wire.reply(("old line\n" * 150 + "last error\n").encode(), 206)
    wire.reply(b"", 404)

    logs = provider.check_logs(PR, ("lint", "test", "external"))
    assert logs["lint"].splitlines() == ["old line"] * 149 + ["last error"]
    assert logs["test"] == "log unavailable: Forgejo REST returned HTTP 404"
    assert logs["external"] == "log unavailable: no Forgejo Actions job link"
    assert [c.target for c in wire.calls[2:]] == [
        f"{REPO}/actions/runs?run_number=15",
        f"{REPO}/actions/runs/314/jobs",
        f"{REPO}/actions/jobs/120/logs",
        f"{REPO}/actions/jobs/121/logs",
    ]
    assert wire.calls[-1].headers["Range"] == f"bytes=-{forgejo_rest.LOG_BYTES}"


def test_missing_credentials_or_wrong_remote_stop_before_request(
    provider: GitProvider, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GIRO_FORGEJO_TOKEN")
    with pytest.raises(ProviderError, match="GIRO_FORGEJO_TOKEN"):
        provider.status(PR)
    monkeypatch.setenv("GIRO_FORGEJO_TOKEN", "fixture-token")
    git(provider.root, "remote", "set-url", "forge", "https://other.example/example/Omnibus-a2ui")
    with pytest.raises(ProviderError, match="must point to an owner/repo"):
        provider.status(PR)
    assert wire.calls == []


def test_refuses_insecure_base_url(
    provider: GitProvider, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIRO_FORGEJO_URL", f"http://{HOST}")
    with pytest.raises(ProviderError, match="HTTPS base URL"):
        provider.status(PR)
    assert wire.calls == []


def test_https_remote_with_instance_prefix(provider: GitProvider, wire: Wire) -> None:
    git(provider.root, "remote", "set-url", "forge", f"{BASE_URL}/example/Omnibus-a2ui.git")
    wire.reply(pull())
    assert provider.status(PR).state == OPEN
    assert wire.calls[0].target == f"{REPO}/pulls/7"


def test_refuses_to_use_a_pr_number_from_old_host(provider: GitProvider, wire: Wire) -> None:
    old = replace(PR, url="https://github.com/example/Omnibus-a2ui/pull/7")
    with pytest.raises(ProviderError, match="URL does not match"):
        provider.comment(old, "update")
    assert wire.calls == []


def test_rest_does_not_follow_redirects_or_report_raw_body(
    provider: GitProvider, wire: Wire
) -> None:
    wire.reply(b"fixture-token", 302, Location="https://other.example/")
    with pytest.raises(ProviderError, match="HTTP 302") as error:
        provider.status(PR)
    assert "fixture-token" not in str(error.value)
    assert len(wire.calls) == 1


def test_response_timeout_and_limit(
    provider: GitProvider, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire.replies.append(TimeoutError("fixture-token"))
    with pytest.raises(ProviderError, match="timed out") as error:
        provider.status(PR)
    assert "fixture-token" not in str(error.value)
    monkeypatch.setattr(forgejo_rest, "MAX_BODY", 8)
    wire.reply(pull())
    with pytest.raises(ProviderError, match="exceeds read limit"):
        provider.status(PR)
