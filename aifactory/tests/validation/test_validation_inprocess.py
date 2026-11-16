"""``validation.inprocess``: a worker call in this process leaves no state behind."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aifactory import harness
from aifactory.run import members
from aifactory.testing import executor
from validation import inprocess


def _state() -> tuple[object, ...]:
    agents = harness.install()
    modules = [harness.load(name) for name in harness.HARNESSES]
    return (
        dict(agents.INTERFACES),
        [(m.run, m.resolve_model) for m in modules],
        executor.execute,
        members.command_prefix(),
        dict(os.environ),
        os.getcwd(),
    )


def _env(tmp_path: Path) -> dict[str, str]:
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"agents": {}}), encoding="utf-8")
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    return {**os.environ, "GIRO_VALIDATE_FAKE": str(script), "GIRO_VALIDATE_HIDDEN": str(hidden)}


@pytest.mark.parametrize(
    ("argv", "code"),
    [(["--version"], 0), (["no-such-command"], 2), (["no-such-command", "--json"], 2)],
)
def test_call_restores_environment_cwd_and_fake_harness(
    tmp_path: Path, argv: list[str], code: int
) -> None:
    before = _state()
    got, out, err = inprocess.call(argv, _env(tmp_path), tmp_path)
    assert got == code, (out, err)
    assert _state() == before
    if code == 0:
        assert out.strip()


def test_call_sees_only_the_step_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def main(argv: list[str]) -> int:
        seen.update(os.environ)
        seen["cwd"] = os.getcwd()
        raise RuntimeError("step crashed")

    monkeypatch.setenv("OUTER_ONLY", "1")
    monkeypatch.setattr("validation.worker.main", main)
    code, _, err = inprocess.call([], {"STEP_ONLY": "1"}, tmp_path)
    assert code == 1 and "step crashed" in err
    assert "OUTER_ONLY" not in seen and seen["STEP_ONLY"] == "1"
    assert Path(seen["cwd"]).resolve() == tmp_path.resolve()
    assert os.environ["OUTER_ONLY"] == "1" and "STEP_ONLY" not in os.environ
