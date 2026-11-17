"""Server-owned checks, independent of the number of browser tabs and reloads."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from starlette.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from aifactory.web.overview import Overview

log = logging.getLogger(__name__)


async def once(check: Callable[[], object]) -> None:
    # Run once on every server start; later runs are explicit (fresh=1).
    try:
        await run_in_threadpool(check)
    except Exception:
        log.exception("Dashboard startup check failed")


async def warm_overview(overview: Overview) -> None:
    # Fills the repository caches so the first page read is not cold.
    try:
        await overview.warm()
    except Exception:
        log.exception("Dashboard overview warm-up failed")


async def periodic(check: Callable[[], object], interval: float) -> None:
    # Run immediately on every server start, including the restart after an upgrade.
    while True:
        try:
            await run_in_threadpool(check)
        except Exception:
            log.exception("Dashboard background check failed")
        await asyncio.sleep(interval)
