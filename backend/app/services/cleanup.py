"""What nobody needs any more, a few times a day.

The frame removes accounts deleted more than 30 days ago (with everything they own, ``services/content.py``). The
features that keep a trash or a history add their own jobs with ``register``: they run in the same round, each on its
own, so one that fails does not keep the others from running. Every deadline goes by the server's clock.

A round comes every ``TICK_SECONDS``; a job runs in it when its own interval (``every``, six hours unless it asks for
less) has passed since it last ran. The first round after the start runs every job.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from ..db import SessionLocal
from . import accounts

logger = logging.getLogger("nextime.cleanup")

#: How often a job runs unless it asks for less.
INTERVAL_SECONDS = 6 * 3600
#: How often the round looks which jobs are due.
TICK_SECONDS = 3600

#: Jobs of the features (the trash, the history), each called with nothing and expected to open its own session.
_jobs: list[tuple[str, Callable[[], None]]] = []
#: The interval of each job by name, and when it last ran (``time.monotonic``).
_every: dict[str, float] = {}
_last: dict[str, float] = {}


def register(name: str, job: Callable[[], None], every: float = INTERVAL_SECONDS) -> None:
    _jobs.append((name, job))
    _every[name] = every


def _purge_accounts() -> None:
    with SessionLocal() as db:
        accounts.purge_deleted(db)


def _all() -> list[tuple[str, Callable[[], None]]]:
    return [("accounts", _purge_accounts), *_jobs]


def _run(name: str, job: Callable[[], None], now: float) -> None:
    _last[name] = now
    try:
        job()
    except Exception:
        logger.exception("Cleaning up failed job=%s", name)


def run_once() -> None:
    """Every job, now."""
    now = time.monotonic()
    for name, job in _all():
        _run(name, job, now)


def run_due(now: float | None = None) -> None:
    """The jobs whose interval has passed since they last ran (all of them the first time)."""
    now = time.monotonic() if now is None else now
    for name, job in _all():
        last = _last.get(name)
        # A minute of slack: a round that comes a moment early does not push a job back by a whole tick.
        if last is None or now - last >= _every.get(name, INTERVAL_SECONDS) - 60:
            _run(name, job, now)


async def run_forever(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_due)
        except Exception:
            logger.exception("Cleaning up failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            return
        except TimeoutError:
            pass
