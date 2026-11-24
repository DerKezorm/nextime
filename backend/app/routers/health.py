from __future__ import annotations

import logging
import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import __version__

router = APIRouter(prefix="/api/health", tags=["health"])
logger = logging.getLogger("nextime.clock")

#: Browsers whose clock is further off than this make the log warn about the server's clock.
DRIFT_WARN_SECONDS = 300
#: One warning per this many seconds is enough.
WARN_EVERY_SECONDS = 3600
_last_warning = 0.0


class BrowserClockIn(BaseModel):
    #: The browser's time in milliseconds since 1970.
    now: int = Field(ge=0, le=10**14)


@router.get("", summary="Is nextime running?")
def health() -> dict[str, str]:
    # ``app`` tells the iPhone app "nextime, but too old" from "no nextime" (TIME-29 EC-2).
    return {"status": "ok", "version": __version__, "app": "nextime"}


@router.get("/clock", summary="The server's time in milliseconds")
def clock() -> dict[str, int]:
    return {"now": int(time.time() * 1000)}


@router.post("/clock", summary="A browser tells its time; the log warns when the server's clock is far off")
def compare_clock(body: BrowserClockIn) -> dict[str, int]:
    """Deadlines (trash, history, read links) always follow the server's clock. A browser's time only tells whether
    that clock is far off, which the operator should know: the log says it, once an hour at most."""
    global _last_warning
    server = time.time()
    drift = int(server - body.now / 1000)
    if abs(drift) > DRIFT_WARN_SECONDS and server - _last_warning > WARN_EVERY_SECONDS:
        _last_warning = server
        logger.warning("The server's clock differs from a browser's by %s seconds; deadlines follow the server's "
                       "clock. Check the time of the server.", drift)
    return {"now": int(server * 1000), "drift": drift}


def forget() -> None:
    global _last_warning
    _last_warning = 0.0
