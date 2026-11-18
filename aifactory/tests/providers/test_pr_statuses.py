"""``GitProvider.statuses``: one PR list query per repository instead of one query per PR.

GitHub (fake ``gh``) and Azure (fake ``az``) list every PR once and pair them by number;
a PR the list leaves ``unknown`` or does not contain is asked on its own, as before. The
backlog sync's PR refresh gives the same result through the list as PR by PR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from az_fake import HEAD as AZ_HEAD
from az_fake import MERGE_SHA as AZ_MERGE
from az_fake import AzLog, install_fake_az, pr_json, prepare_shared_az
from az_fake import reply as az_reply
from gh_fake import GhLog, install_fake_gh, prepare_shared_gh
from gh_fake import reply as gh_reply

from aifactory.config.settings import ProjectSettings
from aifactory.providers import (
    CLOSED,
    CONFLICT,
    MERGEABLE,
    MERGED,
    OPEN,
    GitProvider,
    ProviderError,
    PrStatus,
    PullRequest,
)
from aifactory.providers.azure import AzureProvider
from aifactory.providers.github import LIST_LIMIT, GitHubProvider
from aifactory.review import sync
from aifactory.run.cleanup import RunContext
from aifactory.runstore.rows import SyncPrRow, TaskPrRow
from aifactory.runstore.store import TaskRunStore

HEAD = "a" * 40
MERGE_SHA = "b" * 40
MERGED_AT = "2026-09-27T11:00:05Z"
FIELDS = "state,mergeable,headRefOid,mergeCommit,mergedAt"
AZURE = {"organization": "contoso", "project": "Fab Rikam", "repository": "app"}


@pytest.fixture(scope="module", autouse=True)
def _shared_fakes(tmp_path_factory: pytest.TempPathFactory) -> None:
    prepare_shared_gh(tmp_path_factory.mktemp("ghwarm"))
    prepare_shared_az(tmp_path_factory.mktemp("azwarm"))


def pr(number: int) -> PullRequest:
    return PullRequest(str(number), f"https://h/pull/{number}", f"factory/T{number}-1", "main", "")


def gh_item(number: int, state: str = "OPEN", mergeable: str = "MERGEABLE") -> dict[str, Any]:
    merged = state == "MERGED"
    return {
        "number": number,
        "state": state,
        "mergeable": mergeable,
        "headRefOid": HEAD,
        "mergeCommit": {"oid": MERGE_SHA} if merged else None,
        "mergedAt": MERGED_AT if merged else None,
    }


def gh_view(number: int, state: str = "OPEN", mergeable: str = "MERGEABLE") -> dict[str, Any]:
    item = gh_item(number, state, mergeable)
    del item["number"]
    return gh_reply(item)


def github(tmp_path: Path, sleeps: list[float] | None = None) -> GitHubProvider:
    settings = ProjectSettings.model_validate({"git_provider": "github"})
    out = [] if sleeps is None else sleeps
    return GitHubProvider(tmp_path, settings, attempts=3, delay=0.5, sleep=out.append)


def keyed(log: GhLog | AzLog, key: str) -> list[list[str]]:
    return [argv for argv in log.argvs() if " ".join(argv[: len(key.split())]) == key]


@pytest.mark.parametrize("error", [OSError("cannot start provider"), RuntimeError("failed")])
@pytest.mark.parametrize("listed_success", [False, True])
def test_fallback_failure_preserves_other_results_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError | RuntimeError,
    listed_success: bool,
) -> None:
    provider = github(tmp_path)
    calls: list[str] = []
    success = PrStatus(OPEN, MERGEABLE, head_sha=HEAD)
    later = PrStatus(MERGED, merge_sha=MERGE_SHA)

    def status(request: PullRequest) -> PrStatus:
        calls.append(request.id)
        if request.id == "2":
            raise error
        return success if request.id == "1" else later

    monkeypatch.setattr(provider, "status", status)
    listed = {"1": success, "2": PrStatus(OPEN)} if listed_success else {}

    found = provider._with_listed([pr(1), pr(2), pr(2), pr(3)], listed)

    assert set(found) == {"1", "2", "3"}
    assert found["1"] == success
    failure = found["2"]
    assert isinstance(failure, ProviderError)
    assert failure.code == "status_failed"
    assert failure.message == str(error)
    assert found["3"] == later
    assert calls == (["2", "3"] if listed_success else ["1", "2", "3"])


# ── GitHub ──────────────────────────────────────────────────────────────────────


def test_github_lists_once_and_pairs_by_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = [
        gh_item(9, "CLOSED", "UNKNOWN"),
        gh_item(8, "MERGED", "UNKNOWN"),
        gh_item(7, "OPEN", "CONFLICTING"),
        gh_item(6, "OPEN", "MERGEABLE"),
        gh_item(5, "OPEN", "MERGEABLE"),  # not asked for
    ]
    log = install_fake_gh(tmp_path, monkeypatch, {"pr list": gh_reply(listed)})
    found = github(tmp_path).statuses([pr(6), pr(7), pr(8), pr(9)])
    assert found == {
        "6": PrStatus(OPEN, MERGEABLE, head_sha=HEAD),
        "7": PrStatus(OPEN, CONFLICT, head_sha=HEAD),
        "8": PrStatus(
            MERGED, head_sha=HEAD, merge_sha=MERGE_SHA, merged_at="2026-09-27T11:00:05.000+00:00"
        ),
        "9": PrStatus(CLOSED, head_sha=HEAD),
    }
    limit = str(LIST_LIMIT)
    assert log.argvs() == [
        ["pr", "list", "--state", "all", "--limit", limit, "--json", f"number,{FIELDS}"]
    ]


def test_github_list_equals_one_view_per_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    states = [(6, "OPEN", "MERGEABLE"), (7, "OPEN", "CONFLICTING"), (8, "MERGED", "UNKNOWN")]
    prs = [pr(n) for n, _, _ in states]
    install_fake_gh(
        tmp_path,
        monkeypatch,
        {
            "pr list": gh_reply([gh_item(*s) for s in states]),
            "pr view": [gh_view(*s) for s in states],
        },
    )
    provider = github(tmp_path)
    one_by_one = {p.id: provider.status(p) for p in prs}
    assert provider.statuses(prs) == one_by_one


def test_github_unknown_or_missing_pr_is_asked_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fake_gh(
        tmp_path,
        monkeypatch,
        {
            "pr list": gh_reply([gh_item(6), gh_item(7, "OPEN", "UNKNOWN")]),
            # 7 is computed by now; 300 is older than the list
            "pr view": [gh_view(7, "OPEN", "CONFLICTING"), gh_view(300, "MERGED", "UNKNOWN")],
        },
    )
    found = github(tmp_path).statuses([pr(6), pr(7), pr(300)])
    assert found["6"] == PrStatus(OPEN, MERGEABLE, head_sha=HEAD)
    assert found["7"] == PrStatus(OPEN, CONFLICT, head_sha=HEAD)
    status = found["300"]
    assert isinstance(status, PrStatus) and status.state == MERGED
    assert len(keyed(log, "pr list")) == 1
    assert [argv[2] for argv in keyed(log, "pr view")] == ["7", "300"]


def test_github_failed_list_falls_back_to_one_view_per_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fake_gh(
        tmp_path,
        monkeypatch,
        {
            "pr list": gh_reply("", exit=1, stderr="HTTP 502"),
            "pr view": [gh_view(6), gh_reply("", exit=1, stderr="not found")],
        },
    )
    found = github(tmp_path).statuses([pr(6), pr(7)])
    assert found["6"] == PrStatus(OPEN, MERGEABLE, head_sha=HEAD)
    error = found["7"]
    assert isinstance(error, ProviderError) and error.code == "gh_failed"
    assert [argv[2] for argv in keyed(log, "pr view")] == ["6", "7"]


def test_github_single_pr_is_one_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = install_fake_gh(tmp_path, monkeypatch, {"pr view": gh_view(6)})
    assert github(tmp_path).statuses([pr(6)]) == {"6": PrStatus(OPEN, MERGEABLE, head_sha=HEAD)}
    assert log.argvs() == [["pr", "view", "6", "--json", FIELDS]]


# ── Azure ───────────────────────────────────────────────────────────────────────


def azure(tmp_path: Path) -> AzureProvider:
    settings = ProjectSettings.model_validate({"git_provider": "azure", "azure": AZURE})
    return AzureProvider(tmp_path, settings, attempts=3, delay=0.5, sleep=lambda _: None)


def test_azure_lists_once_and_pairs_by_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    listed = [
        pr_json(id=9, status="abandoned", merge_status=None),
        pr_json(id=8, status="completed", merge=AZ_MERGE),
        pr_json(id=7, merge_status="conflicts"),
        pr_json(id=6, merge_status="queued"),  # unknown: asked on its own
        pr_json(id=5),
    ]
    log = install_fake_az(
        tmp_path,
        monkeypatch,
        {
            "repos pr list": az_reply(listed),
            "repos pr show": az_reply(pr_json(id=6, merge_status="succeeded")),
        },
    )
    provider = azure(tmp_path)
    found = provider.statuses([pr(5), pr(6), pr(7), pr(8), pr(9)])
    assert found["5"] == PrStatus(OPEN, MERGEABLE, head_sha=AZ_HEAD)
    assert found["6"] == PrStatus(OPEN, MERGEABLE, head_sha=AZ_HEAD)
    assert found["7"] == PrStatus(OPEN, CONFLICT, head_sha=AZ_HEAD)
    merged = found["8"]
    assert isinstance(merged, PrStatus) and (merged.state, merged.merge_sha) == (MERGED, AZ_MERGE)
    assert found["9"] == PrStatus(CLOSED, head_sha=AZ_HEAD)
    (listing,) = keyed(log, "repos pr list")
    assert listing[listing.index("--status") + 1] == "all"
    assert listing[listing.index("--repository") + 1] == "app"
    assert [argv[argv.index("--id") + 1] for argv in keyed(log, "repos pr show")] == ["6"]


def test_azure_list_equals_one_show_per_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        pr_json(id=7, merge_status="conflicts"),
        pr_json(id=8, status="completed", merge=AZ_MERGE),
    ]
    install_fake_az(
        tmp_path,
        monkeypatch,
        {
            "repos pr list": az_reply(items),
            "repos pr show": [az_reply(item) for item in items],
        },
    )
    provider = azure(tmp_path)
    prs = [pr(7), pr(8)]
    one_by_one = {p.id: provider.status(p) for p in prs}
    assert provider.statuses(prs) == one_by_one


def test_azure_failed_list_falls_back_to_one_show_per_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fake_az(
        tmp_path,
        monkeypatch,
        {
            "repos pr list": az_reply("", exit=1, stderr="TF400813"),
            "repos pr show": [az_reply(pr_json(id=7)), az_reply(pr_json(id=8))],
        },
    )
    found = azure(tmp_path).statuses([pr(7), pr(8)])
    assert set(found) == {"7", "8"}
    assert all(isinstance(s, PrStatus) and s.state == OPEN for s in found.values())
    assert len(keyed(log, "repos pr show")) == 2


# ── backlog sync: the refresh of open PRs ───────────────────────────────────────


def _rows(store: TaskRunStore) -> None:
    def task_pr(number: int) -> TaskPrRow:
        return TaskPrRow(
            f"factory/T{number}-1", f"M01-S01-T{number:02d}", "github", str(number),
            f"https://h/pull/{number}", "main", "0", "t", "", "open", "now", "now",
        )  # fmt: skip

    for number in (6, 7, 8):
        store.save_pr(task_pr(number))
    store.save_sync_pr(
        SyncPrRow(
            "factory-sync/1",
            "github",
            "9",
            "https://h/pull/9",
            "main",
            "0",
            "sync",
            "",
            "open",
            "[]",
            "now",
            "now",
        )  # fmt: skip
    )


def _refresh(tmp_path: Path, name: str, provider: GitProvider) -> tuple[Any, ...]:
    store = TaskRunStore(tmp_path / f"{name}.db")
    try:
        _rows(store)
        result = sync.SyncResult(pr=None, created=False, updated=False)
        sync._refresh_all(RunContext(tmp_path, None, store, provider), result)  # type: ignore[arg-type]
        prs = [(r.branch, r.state, r.merge_sha) for r in store._prs("1 = 1", ())]
        syncs = [(r.branch, r.state, r.merge_sha) for r in store._sync_prs("1 = 1", ())]
        return result.states, result.warnings, sorted(prs), syncs
    finally:
        store.close()


def test_sync_refresh_through_the_list_equals_pr_by_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    states = {6: ("OPEN", "MERGEABLE"), 7: ("CLOSED", "UNKNOWN"), 8: ("MERGED", "UNKNOWN")}
    states[9] = ("MERGED", "UNKNOWN")  # the sync PR
    log = install_fake_gh(
        tmp_path,
        monkeypatch,
        {
            "pr list": gh_reply([gh_item(n, *s) for n, s in states.items()]),
            # newest first, as the store lists them: 8, 7, 6, then the sync PR 9
            "pr view": [gh_view(n, *states[n]) for n in (8, 7, 6, 9)],
        },
    )
    listed = _refresh(tmp_path, "listed", github(tmp_path))
    assert len(keyed(log, "pr list")) == 1 and keyed(log, "pr view") == []
    one_by_one = github(tmp_path)
    monkeypatch.setattr(one_by_one, "statuses", lambda prs: GitProvider.statuses(one_by_one, prs))
    assert _refresh(tmp_path, "single", one_by_one) == listed
    assert len(keyed(log, "pr view")) == 4
    states_out, warnings, prs, syncs = listed
    assert sorted(s["task_id"] for s in states_out) == ["M01-S01-T07", "M01-S01-T08"]
    assert warnings == []
    assert [state for _, state, _ in prs] == ["open", "closed", "merged"]
    assert syncs == [("factory-sync/1", "merged", MERGE_SHA)]
