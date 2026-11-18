"""Review list batching without hosting, git subprocesses or a listening server."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from aifactory.config import RunConfig
from aifactory.providers import GitProvider, ProviderError, PrStatus
from aifactory.runstore.rows import TaskPrRow
from aifactory.runstore.store import TaskRunStore
from aifactory.web import review

Harness = tuple[Path, TaskRunStore, Mock]


def pr(task: str, number: str, *, state: str = "open", stamp: str = "2026-01-01") -> TaskPrRow:
    return TaskPrRow(
        branch=f"factory/{task}-{number}",
        task_id=task,
        provider="github",
        pr_id=number,
        url=f"https://example.invalid/pr/{number}",
        base="main",
        base_sha="base",
        title=task,
        body="body",
        state=state,
        created_at=stamp,
        updated_at=stamp,
    )


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    store = TaskRunStore(tmp_path / "trace.db")
    provider = Mock(spec=GitProvider)
    provider.status.side_effect = AssertionError("list must not call status")
    rc = SimpleNamespace(
        config=SimpleNamespace(
            settings=SimpleNamespace(git_provider="github", levels=("project", "task"))
        )
    )

    @contextmanager
    def context(_repo: Path) -> Iterator[review._Ctx]:
        yield review._Ctx(
            tmp_path, cast(RunConfig, rc), store, provider, None, ["existing warning"]
        )

    monkeypatch.setattr(review, "_context", context)
    monkeypatch.setattr(review, "_diff", lambda *_args: {})
    try:
        yield tmp_path, store, provider
    finally:
        store.close()


def test_list_batches_selected_unique_prs_once_per_response(harness: Harness) -> None:
    repo, store, provider = harness
    old = pr("one", "old")
    first = pr("one", "1", stamp="2026-01-02")
    second = pr("two", "2")
    shared = pr("three", "2")
    for row in (old, first, second, shared):
        store.save_pr(row)
    provider.statuses.return_value = {
        "1": PrStatus("open", "mergeable"),
        "2": PrStatus("open", "conflict"),
    }

    data, warnings = review.review_list(repo)

    assert provider.statuses.call_count == 1
    requests = provider.statuses.call_args.args[0]
    assert len(requests) == 2
    assert {request.id for request in requests} == {"1", "2"}
    items = {item["task_id"]: item for item in data["prs"]}
    assert set(items) == {"one", "two", "three"}
    assert items["one"]["pr"]["pr_id"] == "1"
    assert items["one"]["mergeability"] == "mergeable"
    assert items["two"]["mergeability"] == items["three"]["mergeability"] == "conflict"
    assert warnings == ["existing warning"]
    assert "done" not in data
    provider.status.assert_not_called()

    provider.statuses.return_value = {"1": PrStatus("closed"), "2": PrStatus("merged")}
    refreshed, _ = review.review_list(repo)
    assert provider.statuses.call_count == 2
    assert {item["provider_state"] for item in refreshed["prs"]} == {"closed", "merged"}
    assert all(item["mergeability"] == "unknown" for item in refreshed["prs"])
    assert all(not item["awaiting_review"] for item in refreshed["prs"])
    assert all(row.state == "open" for row in store.open_prs())


@pytest.mark.parametrize("missing", [False, True])
def test_list_preserves_individual_errors_without_fallback(harness: Harness, missing: bool) -> None:
    repo, store, provider = harness
    first, second = pr("one", "1"), pr("two", "2")
    for row in (first, second):
        store.save_pr(row)
    provider.statuses.return_value = {"1": PrStatus("open", "mergeable")}
    if not missing:
        provider.statuses.return_value["2"] = ProviderError("unavailable", "cannot read PR")

    data, warnings = review.review_list(repo)

    items = {item["task_id"]: item for item in data["prs"]}
    assert items["one"]["provider_state"] == "open"
    assert items["one"]["awaiting_review"] is True
    assert items["two"]["provider_state"] is None
    assert items["two"]["mergeability"] == "unknown"
    assert items["two"]["awaiting_review"] is False
    assert warnings == (
        ["existing warning"]
        if missing
        else ["existing warning", f"PR {second.url}: unavailable: cannot read PR"]
    )
    provider.statuses.assert_called_once()
    provider.status.assert_not_called()


@pytest.mark.parametrize(
    "error", [ProviderError("offline", "failed"), RuntimeError("failed"), OSError("failed")]
)
def test_batch_failure_warns_for_each_pr_without_retry(harness: Harness, error: Exception) -> None:
    repo, store, provider = harness
    rows = [pr("one", "1"), pr("two", "2")]
    for row in rows:
        store.save_pr(row)
    provider.statuses.side_effect = error

    data, warnings = review.review_list(repo)

    assert len(data["prs"]) == 2
    assert all(item["provider_state"] is None for item in data["prs"])
    assert set(warnings) == {"existing warning", *(f"PR {row.url}: {error}" for row in rows)}
    provider.statuses.assert_called_once()
    provider.status.assert_not_called()


def test_done_rows_are_not_batched_and_open_tasks_hide_done_rows(harness: Harness) -> None:
    repo, store, provider = harness
    opened = pr("one", "1")
    for row in (
        opened,
        pr("one", "old", state="closed"),
        pr("two", "2", state="closed"),
        pr("three", "3", state="merged", stamp="2026-01-03"),
    ):
        store.save_pr(row)
    provider.statuses.return_value = {"1": PrStatus("open", "mergeable")}

    data, _ = review.review_list(repo, include_done=True)

    provider.statuses.assert_called_once_with([opened.request()])
    assert [item["task_id"] for item in data["done"]] == ["three", "two"]
    assert [item["provider_state"] for item in data["done"]] == ["merged", "closed"]
    assert all("body" not in item["pr"] for item in data["done"])
    provider.status.assert_not_called()


def test_empty_open_list_does_not_call_provider(harness: Harness) -> None:
    repo, store, provider = harness
    store.save_pr(pr("one", "1", state="closed"))

    data, _ = review.review_list(repo, include_done=True)

    assert data["prs"] == []
    assert len(data["done"]) == 1
    provider.statuses.assert_not_called()
    provider.status.assert_not_called()


def test_detail_keeps_fresh_single_status_and_skips_closed_prs(harness: Harness) -> None:
    repo, store, provider = harness
    row = pr("one", "1")
    store.save_pr(row)
    provider.status.side_effect = None
    provider.status.return_value = PrStatus("open", "mergeable")

    data, _ = review.review_detail(repo, "one")

    assert data["actions"]["approve"] is True
    provider.status.assert_called_once_with(row.request())
    provider.statuses.assert_not_called()
    provider.status.reset_mock()
    store.update_pr(row.branch, state="closed")

    data, _ = review.review_detail(repo, "one")

    assert data["provider_state"] == "closed"
    assert not any(data["actions"].values())
    provider.status.assert_not_called()
    provider.statuses.assert_not_called()
