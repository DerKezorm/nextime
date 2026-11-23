"""The rate of one key (TIME-26 EC-2), as nexnotes has it: 600 requests a minute under ``/api/v1/``, 240 at
``/api/mcp``; past that ``slow_down`` with ``Retry-After``. Counted in the memory of the one process nextime runs in.
Wrong keys are braked per sender like wrong passwords (``security.brake``)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

PER_MINUTE = {"api": 600, "mcp": 240}

_lock = threading.Lock()
_calls: dict[tuple[str, int], deque[float]] = defaultdict(deque)


def allow(where: str, key_id: int, now: float | None = None) -> bool:
    """True while the key stays under its requests of the last minute at ``where``; counts this one."""
    now = time.monotonic() if now is None else now
    with _lock:
        seen = _calls[(where, key_id)]
        while seen and now - seen[0] > 60:
            seen.popleft()
        if len(seen) >= PER_MINUTE[where]:
            return False
        seen.append(now)
        return True


def forget() -> None:
    with _lock:
        _calls.clear()
