"""``giro --help`` and ``giro --version`` start without the dashboard and model stack."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

HEAVY = ("starlette", "uvicorn", "pydantic", "rich")

PROBE = """
import contextlib, io, json, sys
from aifactory.cli import main
with contextlib.redirect_stdout(io.StringIO()):
    try:
        main({argv!r})
    except SystemExit as exc:
        assert exc.code in (0, None), exc.code
print(json.dumps(sorted(name for name in sys.modules if name.split(".")[0] in {heavy!r})))
"""


@pytest.mark.parametrize("argv", [["--help"], ["--version"]])
def test_light_commands_do_not_import_heavy_packages(argv: list[str]) -> None:
    code = PROBE.format(argv=argv, heavy=set(HEAVY))
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []
