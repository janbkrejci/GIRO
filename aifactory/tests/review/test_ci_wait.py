"""Absent-CI opt-out preserves real checks, guards, and both head verifications."""

from types import SimpleNamespace
from typing import cast

import pytest

from aifactory.config.settings import ProjectSettings
from aifactory.providers import ChecksStatus, GitProvider, PrStatus, PullRequest
from aifactory.review import ReviewError, ci

PR = PullRequest("7", "u", "branch", "main", "title")


class Hosting:
    reports_checks = True

    def __init__(self, states: list[str], settings: object) -> None:
        self.settings = settings
        self.states = list(states)
        self.check_reads = 0
        self.heads: list[str | None] = ["head", "head"]

    def checks(self, request: PullRequest) -> ChecksStatus:
        self.check_reads += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return ChecksStatus(state)

    def status(self, request: PullRequest) -> PrStatus:
        head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
        return PrStatus("open", "mergeable", head_sha=head)


@pytest.fixture
def elapsed(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    time = [0.0]

    def sleep(seconds: float) -> None:
        time[0] += seconds

    monkeypatch.setattr(ci, "_clock", lambda: time[0])
    monkeypatch.setattr(ci, "sleep", sleep)
    monkeypatch.setattr(ci, "AUTO_MERGE_CHECKS_APPEAR", 90.0)
    monkeypatch.setattr(ci, "AUTO_MERGE_CHECKS_TIMEOUT", 60.0)
    return time


@pytest.mark.parametrize("settings", [ProjectSettings(), SimpleNamespace(), None])
def test_default_waits_for_delayed_ci(elapsed: list[float], settings: object) -> None:
    hosting = Hosting(["none", "pending", "failing"], settings)
    if settings is None:
        del hosting.settings
    checks = ci.head_checks(cast(GitProvider, hosting), PR, "head")
    assert checks.state == "failing"
    reason = ci.checks_reason(checks)
    assert reason is not None and reason[0] == "checks_failing"
    assert hosting.check_reads == 3
    assert elapsed == [30.0]


def test_default_absent_ci_keeps_full_grace(elapsed: list[float]) -> None:
    hosting = Hosting(["none"], ProjectSettings())
    assert ci.head_checks(cast(GitProvider, hosting), PR, "head").state == "none"
    assert elapsed == [90.0]


def test_opt_out_absent_ci_returns_without_sleep(elapsed: list[float]) -> None:
    hosting = Hosting(["none"], ProjectSettings(wait_for_ci=False))
    assert ci.head_checks(cast(GitProvider, hosting), PR, "head").state == "none"
    assert hosting.check_reads == 1
    assert elapsed == [0.0]


@pytest.mark.parametrize(
    ("states", "expected", "seconds"),
    [
        (["pending", "passing"], "passing", 15.0),
        (["pending"], "pending", 60.0),
        (["failing"], "failing", 0.0),
    ],
)
def test_opt_out_enforces_actual_checks(
    elapsed: list[float], states: list[str], expected: str, seconds: float
) -> None:
    hosting = Hosting(states, ProjectSettings(wait_for_ci=False))
    checks = ci.head_checks(cast(GitProvider, hosting), PR, "head")
    assert checks.state == expected
    reason = ci.checks_reason(checks)
    if expected == "passing":
        assert reason is None
    else:
        assert reason is not None and reason[0] == f"checks_{expected}"
    assert elapsed == [seconds]


@pytest.mark.parametrize("heads", [["old"], [None], ["head", "other"]])
def test_opt_out_still_verifies_head(elapsed: list[float], heads: list[str | None]) -> None:
    hosting = Hosting(["none"], ProjectSettings(wait_for_ci=False))
    hosting.heads = list(heads)
    with pytest.raises(ReviewError, match="checks_head_unconfirmed"):
        ci.head_checks(cast(GitProvider, hosting), PR, "head")
    assert hosting.check_reads == (1 if heads[0] == "head" else 0)


def test_opt_out_still_runs_guard(elapsed: list[float]) -> None:
    hosting = Hosting(["none"], ProjectSettings(wait_for_ci=False))

    def guard() -> None:
        raise ReviewError("auto_merge_disabled", "stopped")

    with pytest.raises(ReviewError, match="auto_merge_disabled"):
        ci.settled_checks(cast(GitProvider, hosting), PR, guard)
    assert elapsed == [0.0]
