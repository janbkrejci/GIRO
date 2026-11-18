"""Bounded preflight reuse without Azure, subprocesses, or wall-clock sleeps."""

import shutil
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from aifactory.config.settings import AzureSettings, ProjectSettings
from aifactory.providers import ProviderError, azure


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    executable = tmp_path / "az.exe"
    executable.write_bytes(b"executable")
    config = tmp_path / "config"
    config.mkdir()
    state = SimpleNamespace(now=10.0, calls=[], fail=None, config=config, executable=executable)
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(config))
    monkeypatch.setenv("GIRO_AZ", str(executable))
    monkeypatch.delenv(azure.PAT_ENV, raising=False)
    monkeypatch.setattr(azure, "WINDOWS", False)
    monkeypatch.setattr(azure, "time", SimpleNamespace(monotonic=lambda: state.now))
    monkeypatch.setattr(shutil, "which", lambda name: name)
    monkeypatch.setattr(azure, "_preflight_cache", {})

    def run(self: azure.AzCli, *args: str) -> str:
        state.calls.append(args)
        if args[0] == state.fail:
            raise azure.AzError(args, 1, "failed")
        return "{}"

    monkeypatch.setattr(azure.AzCli, "run", run)
    return state


def provider(root: Path, az: azure.AzCli | None = None) -> azure.AzureProvider:
    return azure.AzureProvider(
        root,
        ProjectSettings(
            git_provider="azure",
            azure=AzureSettings(organization="org", project="project", repository="repo"),
        ),
        az,
    )


def test_shared_direct_check_ready_and_expiry(tmp_path: Path, context: SimpleNamespace) -> None:
    first = provider(tmp_path)
    first.check()
    first._ready()
    provider(tmp_path).check()
    assert len(context.calls) == 3
    context.now += azure.PREFLIGHT_TTL - 0.01
    first._ready()
    assert len(context.calls) == 3
    context.now += 0.01
    first._ready()
    assert len(context.calls) == 6
    provider(tmp_path)._ready()
    assert len(context.calls) == 6


@pytest.mark.parametrize("stage", ["--version", "extension", "account"])
def test_failed_validation_is_retried(tmp_path: Path, context: SimpleNamespace, stage: str) -> None:
    first = provider(tmp_path)
    first.check()
    context.now += azure.PREFLIGHT_TTL
    context.fail = stage
    with pytest.raises(ProviderError):
        first._ready()
    assert not azure._preflight_cache
    with pytest.raises(ProviderError):
        provider(tmp_path).check()
    context.fail = None
    before = len(context.calls)
    first.check()
    assert len(context.calls) == before + 3


@pytest.mark.parametrize("name", ["AZURE_TENANT_ID", "ARM_CLIENT_SECRET", "PATH", "GIRO_AZ"])
def test_environment_change_revalidates(
    tmp_path: Path, context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    first = provider(tmp_path)
    first.check()
    monkeypatch.setenv(name, "changed")
    first._ready()
    assert len(context.calls) == 6
    if name == "GIRO_AZ":
        assert first.az.executable == "changed"


def test_pat_changes_and_removal_revalidate(
    tmp_path: Path, context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = provider(tmp_path)
    for token in ("secret-one", "secret-two"):
        monkeypatch.setenv(azure.PAT_ENV, token)
        first.check()
    assert len(context.calls) == 4
    assert all(call[0] != "account" for call in context.calls)
    assert "secret" not in repr(azure._preflight_cache)
    monkeypatch.delenv(azure.PAT_ENV)
    first._ready()
    assert len(context.calls) == 7


@pytest.mark.parametrize("name", ["azureProfile.json", "msal_token_cache.bin", "config"])
def test_auth_config_contents_change_and_deletion(
    tmp_path: Path, context: SimpleNamespace, name: str
) -> None:
    first = provider(tmp_path)
    first.check()
    path = context.config / name
    path.write_bytes(b"identity-one")
    first.check()
    path.write_bytes(b"identity-two")
    first.check()
    path.unlink()
    # The original context can still have a valid success within its TTL.
    context.now += azure.PREFLIGHT_TTL
    first.check()
    assert len(context.calls) == 12


def test_unrelated_activity_and_timestamp_touches_do_not_invalidate(
    tmp_path: Path, context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = context.config / "msal_token_cache.bin"
    token.write_bytes(b"identity")
    provider(tmp_path).check()
    token.touch()
    for name in ("commandIndex.json", "az.log", "msal_http_cache.bin"):
        (context.config / name).write_bytes(b"ordinary activity")
    monkeypatch.setenv("UNRELATED_SETTING", "changed")
    provider(tmp_path).check()
    assert len(context.calls) == 3


def test_executable_and_repo_context_changes(tmp_path: Path, context: SimpleNamespace) -> None:
    first = provider(tmp_path)
    first.check()
    context.executable.write_bytes(b"updated executable")
    first.check()
    provider(tmp_path / "other").check()
    changed = provider(tmp_path)
    changed.azure = AzureSettings(organization="other", project="project", repository="repo")
    changed.check()
    assert len(context.calls) == 12


def test_injected_clients_are_private_and_expire(tmp_path: Path, context: SimpleNamespace) -> None:
    class CustomAz(azure.AzCli):
        pass

    provider(tmp_path).check()
    client = CustomAz(tmp_path)
    first = provider(tmp_path, client)
    first.check()
    first._ready()
    provider(tmp_path, client).check()
    assert len(context.calls) == 9
    context.now += azure.PREFLIGHT_TTL
    first._ready()
    assert len(context.calls) == 12


def test_cache_size_is_bounded(
    tmp_path: Path, context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(azure, "PREFLIGHT_MAX_ENTRIES", 2)
    for name in ("one", "two", "three", "one"):
        provider(tmp_path / name).check()
    assert len(azure._preflight_cache) == 2
    assert len(context.calls) == 12


@pytest.mark.parametrize("fail", [False, True])
def test_single_flight_and_failure_release(
    tmp_path: Path, context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    entered, release, waiting = Event(), Event(), Event()
    original = azure.AzCli.run
    original_result = Future.result

    def result(self: Future[None], timeout: float | None = None) -> None:
        waiting.set()
        return original_result(self, timeout)

    def run(self: azure.AzCli, *args: str) -> str:
        if self.cwd == tmp_path and args == ("--version",) and not entered.is_set():
            entered.set()
            assert release.wait(5)
            if fail:
                raise azure.AzError(args, 1, "failed")
        return original(self, *args)

    monkeypatch.setattr(Future, "result", result)
    monkeypatch.setattr(azure.AzCli, "run", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(provider(tmp_path).check)
        try:
            assert entered.wait(5)
            # An unrelated key progresses while the first validation is blocked.
            provider(tmp_path / "other").check()
            second = pool.submit(provider(tmp_path).check)
            assert waiting.wait(5)
        finally:
            release.set()
        if fail:
            with pytest.raises(ProviderError):
                first.result(timeout=5)
            with pytest.raises(ProviderError):
                second.result(timeout=5)
            assert len(context.calls) == 3  # only the unrelated context succeeded
        else:
            first.result(timeout=5)
            second.result(timeout=5)
    provider(tmp_path).check()  # independent retry after failure; hit after success
    assert len(context.calls) == 6
    assert not azure._preflight_running


def test_context_change_during_validation_is_not_published(
    tmp_path: Path, context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = azure.AzCli.run

    def run(self: azure.AzCli, *args: str) -> str:
        result = original(self, *args)
        if args[0] == "account":
            monkeypatch.setenv("AZURE_TENANT_ID", "new-tenant")
        return result

    monkeypatch.setattr(azure.AzCli, "run", run)
    provider(tmp_path).check()
    assert not azure._preflight_cache
    provider(tmp_path).check()
    assert len(context.calls) == 6
