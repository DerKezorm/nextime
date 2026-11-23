"""Whether the server reaches the internet (TIME-1 AC-9, EC-2).

The box "State" on the page "Server" says "Internet reachable, sync running" or warns. One plain question to a fixed
address, at most every few minutes: nothing about accounts, calendars or events goes out. The calendar syncs (iCloud,
Google, subscriptions, TIME-15 to TIME-17) ask ``reachable`` before a round and pause without it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import httpx

from ..config import get_settings

logger = logging.getLogger("nextime.internet")

#: How long an answer counts; the page may ask often.
FRESH_SECONDS = 300
TIMEOUT = httpx.Timeout(5.0, connect=4.0)

_lock = threading.Lock()
_known: tuple[float, bool] | None = None
#: The tests put their own answer here instead of going out.
probe_override: Callable[[], bool] | None = None


def _ask() -> bool:
    if probe_override is not None:
        return probe_override()
    url = get_settings().internet_probe_url
    if not url:
        return False
    try:
        with httpx.Client(timeout=TIMEOUT, trust_env=False, follow_redirects=False) as client:
            client.head(url)
        return True
    except Exception as problem:  # noqa: BLE001
        logger.info("The internet is not reachable: %s", type(problem).__name__)
        return False


def reachable(*, force: bool = False) -> bool:
    global _known
    with _lock:
        now = time.monotonic()
        if not force and _known is not None and now - _known[0] < FRESH_SECONDS:
            return _known[1]
        answer = _ask()
        if _known is not None and _known[1] and not answer:
            logger.warning("The internet is not reachable; syncing pauses until it is back")
        _known = (now, answer)
        return answer


def forget() -> None:
    global _known
    with _lock:
        _known = None
